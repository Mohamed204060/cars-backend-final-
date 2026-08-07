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
    InvalidVctStatusError,
    ManufacturerNotApprovedError,
    NotFoundError,
    approve_manufacturer_via_repository,
    create_generation_via_repository,
    create_trim_via_repository,
    propose_manufacturer_via_repository,
    propose_model_via_repository,
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


class TrimCreateRequest(BaseModel):
    fuel_type_ref_id: str
    transmission_type_ref_id: str


class TrimResponse(BaseModel):
    id: str
    generation_id: str
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
    return GenerationResponse(id=generation.id, model_id=generation.model_id)


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
