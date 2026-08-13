"""
vct_api.py — طبقة REST API لخدمة VCT (كتالوج السيارات)
المرجع: VCT Contract Extension & Implementation Plan؛ REQ-VCT-001..005

يُعيد استخدام بنية الجلسة والأخطاء الموحَّدة من auth_api.py، ونفس فحص
الصلاحية الموضعي المعتمَد في pct_api.py (SYSTEM_ADMIN_ROLES) لعملية approve.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from vct_service import (
    InvalidMarketAvailabilityTargetError,
    InvalidVctStatusError,
    ManufacturerNotApprovedError,
    MarketAvailabilityLevelConflictError,
    NotFoundError,
    TrimNotFoundError,
    YearOutOfGenerationRangeError,
    DuplicateTrimModelYearError,
    add_market_availability_via_repository,
    approve_manufacturer_via_repository,
    create_generation_via_repository,
    create_trim_model_year_via_repository,
    create_trim_via_repository,
    is_trim_available_in_country_via_repository,
    list_approved_manufacturers_via_repository,
    list_approved_models_via_repository,
    list_generations_via_repository,
    list_trims_via_repository,
    propose_manufacturer_via_repository,
    propose_model_via_repository,
    update_generation_year_range_via_repository,
)

router = APIRouter(prefix="/api/v1/vct", tags=["vct"])


class ManufacturerResponse(BaseModel):
    id: str
    status: str


class ModelResponse(BaseModel):
    id: str
    manufacturer_id: str
    status: str


class GenerationResponse(BaseModel):
    id: str
    model_id: str
    start_year: Optional[int] = None
    end_year: Optional[int] = None


class GenerationYearRangeUpdateRequest(BaseModel):
    start_year: Optional[int] = None
    end_year: Optional[int] = None


class TrimCreateRequest(BaseModel):
    fuel_type_ref_id: str
    transmission_type_ref_id: str


class TrimResponse(BaseModel):
    id: str
    generation_id: str
    fuel_type_ref_id: str
    transmission_type_ref_id: str


class TrimModelYearCreateRequest(BaseModel):
    year: int


class TrimModelYearResponse(BaseModel):
    id: str
    trim_ref_id: str
    year: int


class MarketAvailabilityCreateRequest(BaseModel):
    country_ref_id: str


class MarketAvailabilityResponse(BaseModel):
    id: str
    country_ref_id: str
    trim_ref_id: Optional[str] = None
    trim_model_year_ref_id: Optional[str] = None


class ManufacturerListItem(BaseModel):
    id: str
    name: Optional[str] = None


class ModelListItem(BaseModel):
    id: str
    name: Optional[str] = None


class GenerationListItem(BaseModel):
    id: str
    name: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None


class TrimListItem(BaseModel):
    id: str
    name: Optional[str] = None
    fuel_type_ref_id: str
    transmission_type_ref_id: str


def get_vct_repository(request: Request):
    return request.app.state.vct_repository


@router.post("/manufacturers", response_model=ManufacturerResponse, status_code=status.HTTP_201_CREATED)
def propose_manufacturer(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    m = propose_manufacturer_via_repository(vct_repo)
    return ManufacturerResponse(id=m.id, status=m.status)


@router.get("/manufacturers/{manufacturer_id}", response_model=ManufacturerResponse)
def get_manufacturer(
    manufacturer_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    m = vct_repo.get_manufacturer_by_id(manufacturer_id)
    if m is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "MANUFACTURER_NOT_FOUND", "الشركة المصنّعة غير موجودة.")
    return ManufacturerResponse(id=m.id, status=m.status)


@router.post("/manufacturers/{manufacturer_id}/approve", response_model=ManufacturerResponse)
def approve_manufacturer(
    manufacturer_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-VCT-002: مدير النظام فقط — نفس فحص PCT approve حرفيًا.
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")

    try:
        m = approve_manufacturer_via_repository(vct_repo, manufacturer_id=manufacturer_id)
    except ValueError:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "MANUFACTURER_NOT_FOUND", "الشركة المصنّعة غير موجودة.")
    except InvalidVctStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return ManufacturerResponse(id=m.id, status=m.status)


@router.post("/manufacturers/{manufacturer_id}/models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
def propose_model(
    manufacturer_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    try:
        model = propose_model_via_repository(vct_repo, manufacturer_id=manufacturer_id)
    except NotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "MANUFACTURER_NOT_FOUND", str(exc))
    except ManufacturerNotApprovedError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "MANUFACTURER_NOT_APPROVED", str(exc))
    return ModelResponse(id=model.id, manufacturer_id=model.manufacturer_id, status=model.status)


@router.post("/models/{model_id}/generations", response_model=GenerationResponse, status_code=status.HTTP_201_CREATED)
def create_generation(
    model_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    try:
        generation = create_generation_via_repository(vct_repo, model_id=model_id)
    except NotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "MODEL_NOT_FOUND", str(exc))
    return GenerationResponse(id=generation.id, model_id=generation.model_id,
                               start_year=generation.start_year, end_year=generation.end_year)


@router.get("/generations/{generation_id}", response_model=GenerationResponse)
def get_generation(
    generation_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    generation = vct_repo.get_generation_by_id(generation_id)
    if generation is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "GENERATION_NOT_FOUND", "الجيل غير موجود.")
    return GenerationResponse(id=generation.id, model_id=generation.model_id,
                               start_year=generation.start_year, end_year=generation.end_year)


@router.post("/generations/{generation_id}/year-range", response_model=GenerationResponse)
def update_generation_year_range(
    generation_id: str,
    body: GenerationYearRangeUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    """
    Approved VCT Design Baseline §2، §4 (الفقرة الثانية): start_year/
    end_year وصفيان فقط، لكن تعديلهما يُرفَض إن كان سيُخرج أي Trim Model
    Year موجودة فعليًا خارج النطاق الجديد.
    """
    try:
        generation = update_generation_year_range_via_repository(
            vct_repo, generation_id=generation_id, start_year=body.start_year, end_year=body.end_year,
        )
    except NotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "GENERATION_NOT_FOUND", str(exc))
    except YearOutOfGenerationRangeError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "YEAR_RANGE_CONFLICTS_WITH_EXISTING_MODEL_YEARS", str(exc))
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_YEAR_RANGE", str(exc))
    return GenerationResponse(id=generation.id, model_id=generation.model_id,
                               start_year=generation.start_year, end_year=generation.end_year)


@router.post("/generations/{generation_id}/trims", response_model=TrimResponse, status_code=status.HTTP_201_CREATED)
def create_trim(
    generation_id: str,
    body: TrimCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    try:
        trim = create_trim_via_repository(
            vct_repo, generation_id=generation_id,
            fuel_type_ref_id=body.fuel_type_ref_id, transmission_type_ref_id=body.transmission_type_ref_id,
        )
    except NotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "GENERATION_NOT_FOUND", str(exc))
    return TrimResponse(id=trim.id, generation_id=trim.generation_id,
                         fuel_type_ref_id=trim.fuel_type_ref_id, transmission_type_ref_id=trim.transmission_type_ref_id)


@router.get("/trims/{trim_id}", response_model=TrimResponse)
def get_trim(
    trim_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    trim = vct_repo.get_trim_by_id(trim_id)
    if trim is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TRIM_NOT_FOUND", "فئة السيارة غير موجودة.")
    return TrimResponse(id=trim.id, generation_id=trim.generation_id,
                         fuel_type_ref_id=trim.fuel_type_ref_id, transmission_type_ref_id=trim.transmission_type_ref_id)


# ---------------------------------------------------------------------------
# Approved VCT Design Baseline §3-4، Batch 1: Trim Model Years
# ---------------------------------------------------------------------------

@router.post("/trims/{trim_id}/model-years", response_model=TrimModelYearResponse, status_code=status.HTTP_201_CREATED)
def create_trim_model_year(
    trim_id: str,
    body: TrimModelYearCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    try:
        tmy = create_trim_model_year_via_repository(vct_repo, trim_ref_id=trim_id, year=body.year)
    except TrimNotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TRIM_NOT_FOUND", str(exc))
    except YearOutOfGenerationRangeError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "YEAR_OUT_OF_GENERATION_RANGE", str(exc))
    except DuplicateTrimModelYearError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_TRIM_MODEL_YEAR", str(exc))
    return TrimModelYearResponse(id=tmy.id, trim_ref_id=tmy.trim_ref_id, year=tmy.year)


@router.get("/trims/{trim_id}/model-years", response_model=list[TrimModelYearResponse])
def list_trim_model_years(
    trim_id: str,
    correlation_id: str = Depends(get_correlation_id),
    vct_repo=Depends(get_vct_repository),
):
    years = vct_repo.list_trim_model_years_for_trim(trim_id)
    return [TrimModelYearResponse(id=t.id, trim_ref_id=t.trim_ref_id, year=t.year) for t in years]


# ---------------------------------------------------------------------------
# Approved VCT Design Baseline §6-9، 17، Batch 1: Market Availability
# ---------------------------------------------------------------------------

@router.post("/trims/{trim_id}/market-availability", response_model=MarketAvailabilityResponse,
             status_code=status.HTTP_201_CREATED)
def add_trim_market_availability(
    trim_id: str,
    body: MarketAvailabilityCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    try:
        entry = add_market_availability_via_repository(vct_repo, country_ref_id=body.country_ref_id, trim_ref_id=trim_id)
    except TrimNotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TRIM_NOT_FOUND", str(exc))
    except InvalidMarketAvailabilityTargetError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_MARKET_AVAILABILITY_TARGET", str(exc))
    except MarketAvailabilityLevelConflictError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "MARKET_AVAILABILITY_LEVEL_CONFLICT", str(exc))
    return MarketAvailabilityResponse(id=entry.id, country_ref_id=entry.country_ref_id,
                                       trim_ref_id=entry.trim_ref_id, trim_model_year_ref_id=entry.trim_model_year_ref_id)


@router.post("/trim-model-years/{trim_model_year_id}/market-availability", response_model=MarketAvailabilityResponse,
             status_code=status.HTTP_201_CREATED)
def add_trim_model_year_market_availability(
    trim_model_year_id: str,
    body: MarketAvailabilityCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    try:
        entry = add_market_availability_via_repository(
            vct_repo, country_ref_id=body.country_ref_id, trim_model_year_ref_id=trim_model_year_id,
        )
    except TrimNotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TRIM_MODEL_YEAR_NOT_FOUND", str(exc))
    except InvalidMarketAvailabilityTargetError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_MARKET_AVAILABILITY_TARGET", str(exc))
    except MarketAvailabilityLevelConflictError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "MARKET_AVAILABILITY_LEVEL_CONFLICT", str(exc))
    return MarketAvailabilityResponse(id=entry.id, country_ref_id=entry.country_ref_id,
                                       trim_ref_id=entry.trim_ref_id, trim_model_year_ref_id=entry.trim_model_year_ref_id)


@router.get("/trims/{trim_id}/market-availability", response_model=list[MarketAvailabilityResponse])
def list_trim_market_availability(
    trim_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    vct_repo=Depends(get_vct_repository),
):
    rows = vct_repo.get_market_availability_for_target(trim_ref_id=trim_id)
    return [MarketAvailabilityResponse(id=r.id, country_ref_id=r.country_ref_id,
                                        trim_ref_id=r.trim_ref_id, trim_model_year_ref_id=r.trim_model_year_ref_id)
            for r in rows]


# ---------------------------------------------------------------------------
# Batch 1 (Frontend Enablement): قوائم تصفح عامة — بلا جلسة عمدًا، بنفس طبيعة
# GET /search/parts العامة (security: []) — اختيار السيارة جزء من رحلة بحث
# عامة، لا عملية إدارية. فجوة حقيقية اكتُشِفت أثناء بناء الواجهة (لا مسارات
# قائمة كانت موجودة أصلًا)، سدّها هنا ضروري لإكمال الرحلة المطلوبة.
# ---------------------------------------------------------------------------

@router.get("/manufacturers", response_model=list[ManufacturerListItem])
def list_manufacturers(
    correlation_id: str = Depends(get_correlation_id),
    vct_repo=Depends(get_vct_repository),
):
    rows = list_approved_manufacturers_via_repository(vct_repo)
    return [ManufacturerListItem(**r) for r in rows]


@router.get("/manufacturers/{manufacturer_id}/models", response_model=list[ModelListItem])
def list_models_for_manufacturer(
    manufacturer_id: str,
    correlation_id: str = Depends(get_correlation_id),
    vct_repo=Depends(get_vct_repository),
):
    rows = list_approved_models_via_repository(vct_repo, manufacturer_id)
    return [ModelListItem(**r) for r in rows]


@router.get("/models/{model_id}/generations", response_model=list[GenerationListItem])
def list_generations_for_model(
    model_id: str,
    correlation_id: str = Depends(get_correlation_id),
    vct_repo=Depends(get_vct_repository),
):
    rows = list_generations_via_repository(vct_repo, model_id)
    return [GenerationListItem(**r) for r in rows]


@router.get("/generations/{generation_id}/trims", response_model=list[TrimListItem])
def list_trims_for_generation(
    generation_id: str,
    correlation_id: str = Depends(get_correlation_id),
    vct_repo=Depends(get_vct_repository),
):
    rows = list_trims_via_repository(vct_repo, generation_id)
    return [TrimListItem(**r) for r in rows]
