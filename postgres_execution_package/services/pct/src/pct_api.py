"""
pct_api.py — طبقة REST API لخدمة PCT (الكتالوج)
المرجع: PCT Contract Extension & Implementation Plan (المعتمَدة)؛
        api_spec/openapi.yaml؛ REQ-PCT-001..005

يُعيد استخدام بنية الجلسة والأخطاء الموحَّدة من auth_api.py حرفيًا (لا تكرار
منطق)؛ الاعتماد الوحيد الجديد هنا هو فحص الصلاحية الموضعي لِـapprove.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session, get_optional_session
from session_service import Session
from pct_service import (
    DuplicateOemNumberError,
    InvalidCatalogPartStatusError,
    add_localized_name_via_repository,
    add_oem_number_via_repository,
    approve_catalog_part_via_repository,
    list_parts_via_repository,
    propose_catalog_part_via_repository,
)

router = APIRouter(prefix="/api/v1/pct", tags=["pct"])

# REQ-PCT-002: "مدير النظام" — فحص موضعي على iam.users.primary_role (لا RBAC
# عام؛ تأجيله بقرار صريح من مالك المشروع إلى مرحلة مستقلة عند الحاجة).
SYSTEM_ADMIN_ROLES = {"super_admin", "admin"}

PUBLIC_PART_STATUSES = {"approved"}  # CR-015 §0: لا تصفح عام لغير المعتمَد


class CatalogPartCreateRequest(BaseModel):
    category_id: str


class CatalogPartResponse(BaseModel):
    id: str
    category_id: str
    status: str


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class CatalogPartListResponse(BaseModel):
    items: list[CatalogPartResponse]
    pagination: PaginationMeta


class LocalizedNameCreateRequest(BaseModel):
    name_value: str
    name_kind: str
    locale: Optional[str] = None


class LocalizedNameResponse(BaseModel):
    id: str
    name_value: str
    name_kind: str
    locale: Optional[str] = None


class OemNumberCreateRequest(BaseModel):
    manufacturer_ref_id: str
    oem_number: str


class OemNumberResponse(BaseModel):
    id: str
    manufacturer_ref_id: str
    oem_number: str


def get_pct_repository(request: Request):
    return request.app.state.pct_repository


def get_auth_repository_for_role_check(request: Request):
    return request.app.state.auth_repository


def _to_part_response(part) -> CatalogPartResponse:
    return CatalogPartResponse(id=part.id, category_id=part.category_id, status=part.status)


@router.post("/parts", response_model=CatalogPartResponse, status_code=status.HTTP_201_CREATED)
def propose_part(
    body: CatalogPartCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    pct_repo=Depends(get_pct_repository),
):
    try:
        part = propose_catalog_part_via_repository(pct_repo, category_id=body.category_id)
    except Exception as exc:
        if "foreignkeyviolation" in type(exc).__name__.lower() or "foreign key" in str(exc).lower():
            raise error(correlation_id, status.HTTP_404_NOT_FOUND, "CATEGORY_NOT_FOUND", "الفئة المحدَّدة غير موجودة.")
        raise
    return _to_part_response(part)


@router.get("/parts", response_model=CatalogPartListResponse)
def list_parts(
    correlation_id: str = Depends(get_correlation_id),
    optional_session: Optional[Session] = Depends(get_optional_session),
    pct_repo=Depends(get_pct_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    status_filter: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """
    CR-015: يدعم السياقين معًا حسب الحالة والصلاحية (لا Admin-only):
    - status افتراضي (أو صراحة 'approved'): تصفح عام، لا جلسة مطلوبة.
    - status='proposed' أو 'archived': يتطلب جلسة صالحة + دور اعتماد كتالوج
      (SYSTEM_ADMIN_ROLES) — نفس نطاق صلاحية approve الموجودة أصلًا في هذا
      الملف، لا صلاحية جديدة مخترَعة.
    """
    effective_status = status_filter or "approved"

    if effective_status not in PUBLIC_PART_STATUSES:
        if optional_session is None:
            raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "NO_SESSION",
                        "يتطلب عرض هذه الحالة تسجيل الدخول.")
        role = auth_repo.get_user_role(optional_session.user_id)
        if role not in SYSTEM_ADMIN_ROLES:
            raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                        "عرض قطع الكتالوج بهذه الحالة مقصور على مدير النظام.")

    items, total = list_parts_via_repository(pct_repo, status=effective_status, q=q, page=page, page_size=page_size)
    return CatalogPartListResponse(
        items=[_to_part_response(p) for p in items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )


@router.get("/parts/{part_id}", response_model=CatalogPartResponse)
def get_part(
    part_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session | None = Depends(get_optional_session),
    pct_repo=Depends(get_pct_repository),
):
    """
    CR-017: قطعة معتمدة (approved) → عامة بالكامل، بلا جلسة. غير ذلك
    (proposed/archived) → **بلا تغيير عن السلوك الحالي**: يتطلب جلسة صالحة
    (أي جلسة، بلا فحص دور — نفس ما كان عليه الأمر قبل CR-017 تمامًا).
    """
    part = pct_repo.get_part_by_id(part_id)
    if part is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PART_NOT_FOUND", "قطعة الكتالوج غير موجودة.")

    if part.status != "approved" and current_session is None:
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "NO_SESSION",
                    "يتطلب عرض هذه القطعة تسجيل الدخول.")

    return _to_part_response(part)


@router.post("/parts/{part_id}/approve", response_model=CatalogPartResponse)
def approve_part(
    part_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    pct_repo=Depends(get_pct_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-PCT-002: مدير النظام فقط. فحص موضعي على primary_role — راجع
    # SYSTEM_ADMIN_ROLES أعلاه للتوثيق الكامل لهذا القرار.
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "هذه العملية تتطلب صلاحية مدير النظام.")

    try:
        part = approve_catalog_part_via_repository(pct_repo, part_id=part_id)
    except ValueError:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PART_NOT_FOUND", "قطعة الكتالوج غير موجودة.")
    except InvalidCatalogPartStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _to_part_response(part)


@router.post("/parts/{part_id}/names", response_model=LocalizedNameResponse, status_code=status.HTTP_201_CREATED)
def add_name(
    part_id: str,
    body: LocalizedNameCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    pct_repo=Depends(get_pct_repository),
):
    try:
        name = add_localized_name_via_repository(
            pct_repo, part_id=part_id, name_value=body.name_value,
            name_kind=body.name_kind, locale=body.locale,
        )
    except ValueError as exc:
        msg = str(exc)
        if "لا توجد قطعة كتالوج" in msg:
            raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PART_NOT_FOUND", msg)
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_NAME", msg)
    return LocalizedNameResponse(id=name.id, name_value=name.name_value, name_kind=name.name_kind, locale=name.locale)


@router.post("/parts/{part_id}/oem-numbers", response_model=OemNumberResponse, status_code=status.HTTP_201_CREATED)
def add_oem_number(
    part_id: str,
    body: OemNumberCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    pct_repo=Depends(get_pct_repository),
):
    try:
        oem = add_oem_number_via_repository(
            pct_repo, part_id=part_id, manufacturer_ref_id=body.manufacturer_ref_id, oem_number=body.oem_number,
        )
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PART_NOT_FOUND", str(exc))
    except DuplicateOemNumberError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_OEM_NUMBER", str(exc))
    return OemNumberResponse(id=oem.id, manufacturer_ref_id=oem.manufacturer_ref_id, oem_number=oem.oem_number)
