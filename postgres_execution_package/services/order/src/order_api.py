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
from vct_api import get_vct_repository
from session_service import Session
from order_service import (
    DuplicateActiveOfferError,
    InvalidConditionRefError,
    InvalidPurchaseRequestNotesError,
    InvalidPurchaseRequestStatusError,
    OfferNotWithdrawableError,
    PurchaseRequestClosedError,
    TrimModelYearNotValidForPurchaseRequestError,
    TrimNotValidForPurchaseRequestError,
    accept_offer_via_repository,
    cancel_purchase_request_via_repository,
    create_purchase_request_via_repository,
    get_purchase_request_display_via_repository,
    list_my_purchase_requests_via_repository,
    list_my_purchase_requests_display_via_repository,
    list_open_purchase_requests_display_via_repository,
    list_purchase_request_offers_via_repository,
    submit_offer_via_repository,
    withdraw_offer_via_repository,
)

router = APIRouter(prefix="/api/v1", tags=["orders"])


class PurchaseRequestCreateRequest(BaseModel):
    catalog_part_ref_id: str
    trim_ref_id: str
    # Batch 1 (Approved VCT Design Baseline §23): سنة موديل دقيقة اختيارية
    trim_model_year_ref_id: Optional[str] = None
    # CR-022 — النطاق المعتمَد حرفيًا فقط: بلا صور/Media، بلا CR-020 Search.
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
    trim_model_year_ref_id: Optional[str] = None  # Batch 1


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
    Optional (غياب توطين = None صريح).

    Batch 1: generation_name/trim_name/model_year/condition_code/notes
    أصبحت متاحة الآن (كانت مؤجَّلة صراحةً بانتظار اكتمال VCT — Approved
    VCT Design Baseline يحل ذلك الآن). لا Raw IDs في الواجهة حيث يوجد اسم
    حقيقي يمكن حله (trim_ref_id/model_id/manufacturer_id/generation_id/
    trim_model_year_ref_id/condition_ref_id تبقى موجودة كإشارات احتياطية
    فقط لما لا يملك اسمًا محلولًا بعد، لا كبديل عن الاسم عند توفره).
    """
    id: str
    business_code: Optional[str] = None
    status: str
    created_at: str
    catalog_part_ref_id: str
    part_name: Optional[str] = None
    trim_ref_id: str
    trim_name: Optional[str] = None
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    manufacturer_id: Optional[str] = None
    manufacturer_name: Optional[str] = None
    generation_id: Optional[str] = None
    generation_name: Optional[str] = None
    trim_model_year_ref_id: Optional[str] = None
    model_year: Optional[int] = None
    condition_ref_id: Optional[str] = None
    condition_code: Optional[str] = None
    notes: Optional[str] = None


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


class OfferDisplayResponse(BaseModel):
    """
    Batch 1 (Offers Integration): العرض + سياق الطلب المحلول (لا Raw IDs
    حيث يوجد اسم حقيقي). offers.notes تُعرَض كما هي Plain Text (بلا صور —
    Media Foundation ضمن Batch 2). Scoping مطابق تمامًا لـ
    GET /purchase-requests/{prId}/offers (نفس الدالة الأساسية).
    """
    id: str
    business_code: Optional[str] = None
    seller_store_ref_id: str
    amount: float
    currency: str
    provides_shipping: bool
    seller_notes: Optional[str] = None
    status: str
    # سياق الطلب (نفس حقول PurchaseRequestDisplayResponse ذات الصلة)
    purchase_request_id: str
    purchase_request_status: str
    part_name: Optional[str] = None
    trim_name: Optional[str] = None
    model_name: Optional[str] = None
    manufacturer_name: Optional[str] = None
    generation_name: Optional[str] = None
    model_year: Optional[int] = None
    condition_code: Optional[str] = None
    buyer_notes: Optional[str] = None


class OfferDisplayListResponse(BaseModel):
    items: list[OfferDisplayResponse]
    pagination: PaginationMeta


def get_order_repository(request: Request):
    return request.app.state.order_repository


def _pr_response(pr) -> PurchaseRequestResponse:
    return PurchaseRequestResponse(
        id=pr.id, business_code=pr.business_code, buyer_user_ref_id=pr.buyer_user_ref_id,
        catalog_part_ref_id=pr.catalog_part_ref_id, trim_ref_id=pr.trim_ref_id, status=pr.status,
        condition_ref_id=pr.condition_ref_id, notes=pr.notes,
        trim_model_year_ref_id=pr.trim_model_year_ref_id,
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
    vct_repo=Depends(get_vct_repository),
):
    def _is_trim_model_year_valid_for_trim(tmy_id: str, trim_id: str) -> bool:
        tmy = vct_repo.get_trim_model_year_by_id(tmy_id)
        return tmy is not None and tmy.trim_ref_id == trim_id

    try:
        pr = create_purchase_request_via_repository(
            order_repo, buyer_user_ref_id=current_session.user_id,
            catalog_part_ref_id=body.catalog_part_ref_id, trim_ref_id=body.trim_ref_id,
            is_part_approved_checker=pct_repo.is_part_approved,
            condition_ref_id=body.condition_ref_id, notes=body.notes,
            is_condition_ref_valid_checker=lambda ref_id: ref_repo.is_value_of_type(ref_id, "part_condition"),
            is_trim_valid_checker=vct_repo.is_trim_valid,
            trim_model_year_ref_id=body.trim_model_year_ref_id,
            is_trim_model_year_valid_for_trim_checker=_is_trim_model_year_valid_for_trim,
        )
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "PART_NOT_APPROVED", str(exc))
    except TrimNotValidForPurchaseRequestError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TRIM_NOT_FOUND", str(exc))
    except TrimModelYearNotValidForPurchaseRequestError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TRIM_MODEL_YEAR_NOT_FOUND", str(exc))
    except InvalidConditionRefError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_CONDITION_REF", str(exc))
    except InvalidPurchaseRequestNotesError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "NOTES_TOO_LONG", str(exc))
    return _pr_response(pr)


@router.get("/purchase-requests", response_model=PurchaseRequestDisplayListResponse)
def list_open_purchase_requests(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    order_repo=Depends(get_order_repository),
    page: int = 1,
    page_size: int = 20,
):
    """
    Unit 4+5 — فجوة حقيقية مكتشَفة (REQ-PUR-011): تصفح البائع للطلبات
    المفتوحة (status='open' حصرًا) عبر Read Model المحلول (نفس عقد
    /purchase-requests/mine/display)، ليتمكَّن من تقديم عرض (submit_offer)
    على طلب لم يُنشئه هو. Scoping بالجلسة فقط (sessionAuth) — لا فحص دور
    ولا اشتراط ملكية متجر للتصفح نفسه (الاشتراط يبقى عند تقديم العرض،
    كما في submit_offer الحالي). مسار مستقل تمامًا عن POST بنفس الجذر
    وعن GET /purchase-requests/mine — بلا أي تعديل على أي منهما.
    """
    items, total = list_open_purchase_requests_display_via_repository(
        order_repo, page=page, page_size=page_size,
    )
    return PurchaseRequestDisplayListResponse(
        items=[
            PurchaseRequestDisplayResponse(
                id=i.id, business_code=i.business_code, status=i.status,
                created_at=i.created_at.isoformat(),
                catalog_part_ref_id=i.catalog_part_ref_id, part_name=i.part_name,
                trim_ref_id=i.trim_ref_id, trim_name=i.trim_name,
                model_id=i.model_id, model_name=i.model_name,
                manufacturer_id=i.manufacturer_id, manufacturer_name=i.manufacturer_name,
                generation_id=i.generation_id, generation_name=i.generation_name,
                trim_model_year_ref_id=i.trim_model_year_ref_id, model_year=i.model_year,
                condition_ref_id=i.condition_ref_id, condition_code=i.condition_code,
                notes=i.notes,
            )
            for i in items
        ],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )


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
    جديد بدل تغيير عقد قائم، أقل خطورة). Batch 1: يعيد الآن أيضًا
    trim_name/generation_name/model_year/condition_code/notes — كانت
    مؤجَّلة صراحةً بانتظار اكتمال VCT (Approved VCT Design Baseline).
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
                trim_ref_id=i.trim_ref_id, trim_name=i.trim_name,
                model_id=i.model_id, model_name=i.model_name,
                manufacturer_id=i.manufacturer_id, manufacturer_name=i.manufacturer_name,
                generation_id=i.generation_id, generation_name=i.generation_name,
                trim_model_year_ref_id=i.trim_model_year_ref_id, model_year=i.model_year,
                condition_ref_id=i.condition_ref_id, condition_code=i.condition_code,
                notes=i.notes,
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


@router.get("/purchase-requests/{prId}/offers/display", response_model=OfferDisplayListResponse)
def list_purchase_request_offers_display(
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
    Batch 1 — Offers Integration: نفس Scoping تمامًا (المشتري يرى كل
    العروض، البائع يرى عرضه فقط)، لكن كل عرض يحمل سياق الطلب المحلول
    (اسم القطعة/السيارة/الفئة/السنة/الحالة/ملاحظات المشتري) بدل تركه
    للعميل ليستدعي مسارًا منفصلًا لكل عرض (لا N+1: سياق الطلب يُجلَب مرة
    واحدة فقط، لأن كل العروض في هذه القائمة تخص نفس الطلب دائمًا).
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

    pr_display = get_purchase_request_display_via_repository(order_repo, prId)

    def _to_offer_display(offer) -> OfferDisplayResponse:
        return OfferDisplayResponse(
            id=offer.id, business_code=offer.business_code, seller_store_ref_id=offer.seller_store_ref_id,
            amount=offer.amount, currency=offer.currency, provides_shipping=offer.provides_shipping,
            seller_notes=offer.notes, status=offer.status,
            purchase_request_id=prId,
            purchase_request_status=pr_display.status if pr_display else "",
            part_name=pr_display.part_name if pr_display else None,
            trim_name=pr_display.trim_name if pr_display else None,
            model_name=pr_display.model_name if pr_display else None,
            manufacturer_name=pr_display.manufacturer_name if pr_display else None,
            generation_name=pr_display.generation_name if pr_display else None,
            model_year=pr_display.model_year if pr_display else None,
            condition_code=pr_display.condition_code if pr_display else None,
            buyer_notes=pr_display.notes if pr_display else None,
        )

    return OfferDisplayListResponse(
        items=[_to_offer_display(o) for o in items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )
