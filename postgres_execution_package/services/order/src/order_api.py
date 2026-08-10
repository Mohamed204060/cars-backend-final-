"""
order_api.py — طبقة REST API لخدمة الطلبات وعروض الأسعار (PUR)
المرجع: Orders/Messaging/Notifications Contract Extension؛ REQ-PUR-001..018

نموذج الصلاحيات: ملكية فعلية لا دور — المشتري (buyer_user_ref_id) لإلغاء/
قبول طلبه هو، والبائع (عبر متجره المشتق من الجلسة، كما في Inventory) لتقديم/
سحب عروضه هو. لا فحص دور إداري في أي عملية هنا.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from auth_api import error, get_correlation_id, get_current_session
from pct_api import get_pct_repository
from store_api import get_store_repository
from ref_api import get_ref_repository
from session_service import Session
from order_service import (
    DuplicateActiveOfferError,
    InvalidConditionRefError,
    InvalidPurchaseRequestNotesError,
    InvalidPurchaseRequestStatusError,
    OfferNotWithdrawableError,
    PurchaseRequestClosedError,
    accept_offer_via_repository,
    cancel_purchase_request_via_repository,
    create_purchase_request_via_repository,
    list_my_purchase_requests_via_repository,
    list_my_purchase_requests_display_via_repository,
    list_purchase_request_offers_via_repository,
    submit_offer_via_repository,
    withdraw_offer_via_repository,
)

router = APIRouter(prefix="/api/v1", tags=["orders"])


class PurchaseRequestCreateRequest(BaseModel):
    catalog_part_ref_id: str
    trim_ref_id: str
    # CR-022 — النطاق المعتمَد حرفيًا فقط: بلا صور/Media، بلا VCT، بلا CR-020 Search.
    condition_ref_id: Optional[str] = None  # NULL = بلا تفضيل
    notes: Optional[str] = Field(default=None, max_length=2000)  # نص عادي، حد 2000 حرف على مستوى API


class PurchaseRequestResponse(BaseModel):
    id: str
    business_code: Optional[str] = None
    buyer_user_ref_id: str
    catalog_part_ref_id: str
    trim_ref_id: str
    status: str
    condition_ref_id: Optional[str] = None  # CR-022
    notes: Optional[str] = None             # CR-022


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class PurchaseRequestListResponse(BaseModel):
    items: list[PurchaseRequestResponse]
    pagination: PaginationMeta


class PurchaseRequestDisplayResponse(BaseModel):
    """
    CR-021: Read Model عرض فقط — منفصلة تمامًا عن PurchaseRequestResponse
    (لا حقول كتابة، لا استخدام في أي مسار قبول/رفض/إلغاء). كل حقل اسم
    Optional (غياب توطين = None صريح). عمدًا بلا generation_name/trim_label/
    year — غير موجودة في نموذج البيانات، لا تُقدَّم كـnull إيحاءً باكتمالها.
    """
    id: str
    business_code: Optional[str] = None
    status: str
    created_at: str
    catalog_part_ref_id: str
    part_name: Optional[str] = None
    trim_ref_id: str
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    manufacturer_id: Optional[str] = None
    manufacturer_name: Optional[str] = None


class PurchaseRequestDisplayListResponse(BaseModel):
    items: list[PurchaseRequestDisplayResponse]
    pagination: PaginationMeta


class OfferSubmitRequest(BaseModel):
    amount: float
    currency: str
    provides_shipping: bool
    notes: Optional[str] = None


class OfferSubmitResponse(BaseModel):
    id: str
    business_code: Optional[str] = None
    status: str


class OfferResponse(BaseModel):
    id: str
    business_code: Optional[str] = None
    purchase_request_id: str
    seller_store_ref_id: str
    amount: float
    currency: str
    provides_shipping: bool
    notes: Optional[str] = None
    status: str


class OfferListResponse(BaseModel):
    items: list[OfferResponse]
    pagination: PaginationMeta


def get_order_repository(request: Request):
    return request.app.state.order_repository


def _pr_response(pr) -> PurchaseRequestResponse:
    return PurchaseRequestResponse(
        id=pr.id, business_code=pr.business_code, buyer_user_ref_id=pr.buyer_user_ref_id,
        catalog_part_ref_id=pr.catalog_part_ref_id, trim_ref_id=pr.trim_ref_id, status=pr.status,
        condition_ref_id=pr.condition_ref_id, notes=pr.notes,
    )


def _offer_response(offer) -> OfferResponse:
    return OfferResponse(
        id=offer.id, business_code=offer.business_code, purchase_request_id=offer.purchase_request_id,
        seller_store_ref_id=offer.seller_store_ref_id, amount=offer.amount, currency=offer.currency,
        provides_shipping=offer.provides_shipping, notes=offer.notes, status=offer.status,
    )


@router.post("/purchase-requests", response_model=PurchaseRequestResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_request(
    body: PurchaseRequestCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
    pct_repo=Depends(get_pct_repository),
    ref_repo=Depends(get_ref_repository),
):
    try:
        pr = create_purchase_request_via_repository(
            order_repo, buyer_user_ref_id=current_session.user_id,
            catalog_part_ref_id=body.catalog_part_ref_id, trim_ref_id=body.trim_ref_id,
            is_part_approved_checker=pct_repo.is_part_approved,
            condition_ref_id=body.condition_ref_id, notes=body.notes,
            is_condition_ref_valid_checker=lambda ref_id: ref_repo.is_value_of_type(ref_id, "part_condition"),
        )
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "PART_NOT_APPROVED", str(exc))
    except InvalidConditionRefError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_CONDITION_REF", str(exc))
    except InvalidPurchaseRequestNotesError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "NOTES_TOO_LONG", str(exc))
    return _pr_response(pr)


@router.get("/purchase-requests/mine", response_model=PurchaseRequestListResponse)
def list_my_purchase_requests(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
    status_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    items, total = list_my_purchase_requests_via_repository(
        order_repo, buyer_user_ref_id=current_session.user_id,
        status=status_filter, page=page, page_size=page_size,
    )
    return PurchaseRequestListResponse(
        items=[_pr_response(pr) for pr in items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )


@router.get("/purchase-requests/mine/display", response_model=PurchaseRequestDisplayListResponse)
def list_my_purchase_requests_display(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
    page: int = 1,
    page_size: int = 20,
):
    """
    CR-021 — Read Model منفصل تمامًا عن GET /purchase-requests/mine (بلا
    تعديل عليه إطلاقًا؛ فُحصت استخداماته الحالية أولًا وقُرِّر Endpoint
    جديد بدل تغيير عقد قائم، أقل خطورة). يعيد أسماء القطعة/الموديل/الشركة
    المصنِّعة الحقيقية عبر استعلام واحد مجمَّع — لا generation_name ولا
    trim_label ولا year (غير موجودة في نموذج البيانات، بانتظار قرار
    Vehicle Taxonomy منفصل؛ لا تُقدَّم هنا كحقول null إيحاءً باكتمالها).
    """
    items, total = list_my_purchase_requests_display_via_repository(
        order_repo, buyer_user_ref_id=current_session.user_id, page=page, page_size=page_size,
    )
    return PurchaseRequestDisplayListResponse(
        items=[
            PurchaseRequestDisplayResponse(
                id=i.id, business_code=i.business_code, status=i.status,
                created_at=i.created_at.isoformat(),
                catalog_part_ref_id=i.catalog_part_ref_id, part_name=i.part_name,
                trim_ref_id=i.trim_ref_id, model_id=i.model_id, model_name=i.model_name,
                manufacturer_id=i.manufacturer_id, manufacturer_name=i.manufacturer_name,
            )
            for i in items
        ],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )


@router.get("/purchase-requests/{pr_id}", response_model=PurchaseRequestResponse)
def get_purchase_request(
    pr_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
):
    pr = order_repo.get_purchase_request_by_id(pr_id)
    if pr is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PURCHASE_REQUEST_NOT_FOUND", "طلب الشراء غير موجود.")
    return _pr_response(pr)


@router.post("/purchase-requests/{pr_id}/cancel", response_model=PurchaseRequestResponse)
def cancel_purchase_request(
    pr_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
):
    existing = order_repo.get_purchase_request_by_id(pr_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PURCHASE_REQUEST_NOT_FOUND", "طلب الشراء غير موجود.")
    # REQ-PUR-009: المشتري صاحب الطلب حصرًا
    if existing.buyer_user_ref_id != current_session.user_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "لا يجوز إلغاء طلب شراء لا تملكه.")

    try:
        pr = cancel_purchase_request_via_repository(order_repo, pr_id=pr_id)
    except InvalidPurchaseRequestStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _pr_response(pr)


@router.post("/purchase-requests/{requestId}/offers", response_model=OfferSubmitResponse, status_code=status.HTTP_201_CREATED)
def submit_offer(
    requestId: str,
    body: OfferSubmitRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
    store_repo=Depends(get_store_repository),
):
    # REQ-PUR-011: البائع المؤهَّل؛ seller_store_ref_id يُشتَق من الجلسة، لا من الطلب (نفس نمط /inventory-items)
    store = store_repo.get_store_by_owner_id(current_session.user_id)
    if store is None or store.status != "active":
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "STORE_NOT_ACTIVE_OR_NOT_OWNED",
                    "لا يوجد متجر نشط مملوك لهذا المستخدم.")

    try:
        offer = submit_offer_via_repository(
            order_repo, pr_id=requestId, seller_store_ref_id=store.id,
            amount=body.amount, currency=body.currency,
            provides_shipping=body.provides_shipping, notes=body.notes,
        )
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PURCHASE_REQUEST_NOT_FOUND", str(exc))
    except PurchaseRequestClosedError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "PURCHASE_REQUEST_CLOSED", str(exc))
    except DuplicateActiveOfferError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_ACTIVE_OFFER", str(exc))
    return OfferSubmitResponse(id=offer.id, business_code=offer.business_code, status=offer.status)


@router.post("/offers/{offer_id}/accept", response_model=PurchaseRequestResponse)
def accept_offer(
    offer_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
):
    offer = order_repo.get_offer_by_id(offer_id)
    if offer is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "OFFER_NOT_FOUND", "العرض غير موجود.")
    pr = order_repo.get_purchase_request_by_id(offer.purchase_request_id)
    if pr is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PURCHASE_REQUEST_NOT_FOUND", "طلب الشراء غير موجود.")
    # REQ-PUR-013: المشتري صاحب الطلب حصرًا
    if pr.buyer_user_ref_id != current_session.user_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "لا يجوز قبول عرض على طلب لا تملكه.")

    try:
        updated_pr = accept_offer_via_repository(order_repo, pr_id=pr.id, offer_id=offer_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_OFFER_STATE", str(exc))
    return _pr_response(updated_pr)


@router.post("/offers/{offer_id}/withdraw", response_model=OfferResponse)
def withdraw_offer(
    offer_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
    store_repo=Depends(get_store_repository),
):
    offer = order_repo.get_offer_by_id(offer_id)
    if offer is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "OFFER_NOT_FOUND", "العرض غير موجود.")
    # REQ-PUR-018: البائع صاحب العرض حصرًا (عبر ملكية المتجر)
    store = store_repo.get_store_by_owner_id(current_session.user_id)
    if store is None or store.id != offer.seller_store_ref_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "لا يجوز سحب عرض لا تملكه.")

    try:
        updated_offer = withdraw_offer_via_repository(order_repo, offer_id=offer_id)
    except OfferNotWithdrawableError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "OFFER_NOT_WITHDRAWABLE", str(exc))
    return _offer_response(updated_offer)


# ---------------------------------------------------------------------------
# CR-015: Frontend Enablement — قائمتان جديدتان، بلا تعديل على أي Endpoint أعلاه
# ---------------------------------------------------------------------------

@router.get("/purchase-requests/{prId}/offers", response_model=OfferListResponse)
def list_purchase_request_offers(
    prId: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
    store_repo=Depends(get_store_repository),
    status_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """
    نطاق النتيجة حسب صلاحية الطالب (Scoping — لا فحص دور): صاحب طلب الشراء
    يرى كل العروض؛ بائع (يملك متجرًا) يرى عرضه هو فقط ضمن هذا الطلب.
    """
    requester_store = store_repo.get_store_by_owner_id(current_session.user_id)
    requester_store_id = requester_store.id if requester_store is not None else None

    try:
        items, total = list_purchase_request_offers_via_repository(
            order_repo, pr_id=prId, requester_user_ref_id=current_session.user_id,
            requester_store_id=requester_store_id, status=status_filter, page=page, page_size=page_size,
        )
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "PURCHASE_REQUEST_NOT_FOUND", str(exc))
    except PermissionError as exc:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", str(exc))

    return OfferListResponse(
        items=[_offer_response(o) for o in items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )
