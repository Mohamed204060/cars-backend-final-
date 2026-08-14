"""
store_api.py — طبقة REST API لخدمة المتاجر (STR)
المرجع: Store + Inventory Contract Extension؛ REQ-STR-001, 004, 006

نموذج الصلاحيات (مختلف عمدًا عن PCT/VCT/CMP، تحقَّق منه صراحةً في SRS):
- تغيير حالة المتجر (REQ-STR-004): مدير النظام **أو المشرف** (super_admin،
  admin، moderator) — لا مالك المتجر نفسه.
- نقل الملكية (REQ-STR-006): مدير النظام حصريًا (super_admin، admin فقط،
  بلا moderator) — نفس SYSTEM_ADMIN_ROLES من PCT/VCT/CMP.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session, get_optional_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from store_service import (
    InvalidStatusTransitionError,
    UnauthorizedOwnershipTransferError,
    create_store_via_repository,
    transfer_ownership_via_repository,
    transition_store_status_via_repository,
)

router = APIRouter(prefix="/api/v1/store", tags=["store"])

# REQ-STR-004: مدير النظام أو المشرف (أوسع من SYSTEM_ADMIN_ROLES بإضافة moderator)
STORE_STATUS_CHANGE_ROLES = SYSTEM_ADMIN_ROLES | {"moderator"}


class StoreCreateRequest(BaseModel):
    country_ref_id: Optional[str] = None
    city_ref_id: Optional[str] = None


class StoreResponse(BaseModel):
    id: str
    owner_user_ref_id: str
    status: str
    country_ref_id: Optional[str] = None
    city_ref_id: Optional[str] = None


class StorePublicResponse(BaseModel):
    """CR-017: عرض عام — يستبعد owner_user_ref_id عمدًا (تسريب خصوصية:
    يكشف هوية صاحب المتجر). مقصور على المتاجر status='active' فقط؛ غير
    ذلك 404 (لا كشف وجود متجر معلَّق/قيد الإنشاء/مؤرشَف للعموم)."""
    id: str
    status: str
    country_ref_id: Optional[str] = None
    city_ref_id: Optional[str] = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class StoreListResponse(BaseModel):
    items: list[StoreResponse]
    pagination: PaginationMeta


class StoreStatusUpdateRequest(BaseModel):
    new_status: str


class StoreTransferOwnershipRequest(BaseModel):
    new_owner_user_ref_id: str


def get_store_repository(request: Request):
    return request.app.state.store_repository


def _to_response(store) -> StoreResponse:
    return StoreResponse(id=store.id, owner_user_ref_id=store.owner_user_ref_id, status=store.status,
                          country_ref_id=store.country_ref_id, city_ref_id=store.city_ref_id)


def _to_public_response(store) -> StorePublicResponse:
    return StorePublicResponse(id=store.id, status=store.status,
                                country_ref_id=store.country_ref_id, city_ref_id=store.city_ref_id)


@router.post("/stores", response_model=StoreResponse, status_code=status.HTTP_201_CREATED)
def create_store(
    body: StoreCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    store_repo=Depends(get_store_repository),
):
    """
    ملاحظة نطاق: REQ-STR-001 ينص على إنشاء تلقائي عند تسجيل بائع جديد (حدث
    مرتبط بالتسجيل، لا استدعاء REST مباشر مثاليًا). هذا Endpoint بديل مؤقَّت
    يدوي حتى يُربَط الإنشاء التلقائي بحدث التسجيل الفعلي في IAM لاحقًا؛
    المالك = المستخدم الحالي دائمًا (لا يمكن إنشاء متجر لمستخدم آخر عبره).
    """
    store = create_store_via_repository(
        store_repo, owner_user_ref_id=current_session.user_id,
        country_ref_id=body.country_ref_id, city_ref_id=body.city_ref_id,
    )
    return _to_response(store)


@router.get("/stores/{store_id}", response_model=None)
def get_store(
    store_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session | None = Depends(get_optional_session),
    store_repo=Depends(get_store_repository),
) -> StoreResponse | StorePublicResponse:
    """
    CR-017: جلسة موجودة → **بلا تغيير عن السلوك الحالي إطلاقًا** (StoreResponse
    الكاملة، أي حالة، بلا فحص ownership أو دور — نفس ما كان عليه الأمر قبل
    CR-017 تمامًا). لا جلسة → عام، مقصور على status='active' فقط (404 لغير
    ذلك، لا كشف وجود متجر معلَّق/مؤرشَف)، StorePublicResponse (بلا
    owner_user_ref_id).
    """
    store = store_repo.get_store_by_id(store_id)
    if store is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "STORE_NOT_FOUND", "المتجر غير موجود.")

    if current_session is not None:
        return _to_response(store)

    if store.status != "active":
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "STORE_NOT_FOUND", "المتجر غير موجود.")
    return _to_public_response(store)


@router.get("/stores", response_model=StoreListResponse)
def list_stores(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    store_repo=Depends(get_store_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    status_filter: Optional[str] = None,
    owner_ref_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """
    CR-015: استخدام إداري أساسًا فقط (§0 من الـCR) — لا Directory عام هنا.
    نفس مجموعة الأدوار المخوَّلة بتغيير حالة المتجر (STORE_STATUS_CHANGE_ROLES:
    مدير النظام أو المشرف) — لا معنى لمنح مشرف صلاحية تغيير الحالة بلا
    إمكانية رؤية القائمة أصلًا.
    """
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in STORE_STATUS_CHANGE_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "عرض قائمة المتاجر مقصور على مدير النظام أو المشرف.")

    items, total = store_repo.list_stores(status_filter, owner_ref_id, page, page_size)
    return StoreListResponse(
        items=[_to_response(s) for s in items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )


@router.post("/stores/{store_id}/status", response_model=StoreResponse)
def update_store_status(
    store_id: str,
    body: StoreStatusUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    store_repo=Depends(get_store_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-STR-004: مدير النظام أو المشرف فقط
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in STORE_STATUS_CHANGE_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "تغيير حالة المتجر مقصور على مدير النظام أو المشرف.")

    try:
        store = transition_store_status_via_repository(store_repo, store_id=store_id, new_status=body.new_status)
    except ValueError as exc:
        if "لا يوجد متجر" in str(exc):
            raise error(correlation_id, status.HTTP_404_NOT_FOUND, "STORE_NOT_FOUND", str(exc))
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_STATUS", str(exc))
    except InvalidStatusTransitionError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _to_response(store)


@router.post("/stores/{store_id}/transfer-ownership", response_model=StoreResponse)
def transfer_ownership(
    store_id: str,
    body: StoreTransferOwnershipRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    store_repo=Depends(get_store_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-STR-006: مدير النظام حصريًا (بلا moderator)
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "نقل ملكية المتجر مقصور على مدير النظام.")

    try:
        store = transfer_ownership_via_repository(
            store_repo, store_id=store_id, new_owner_user_ref_id=body.new_owner_user_ref_id, actor_role="admin",
        )
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "STORE_NOT_FOUND", str(exc))
    except UnauthorizedOwnershipTransferError as exc:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", str(exc))
    return _to_response(store)
