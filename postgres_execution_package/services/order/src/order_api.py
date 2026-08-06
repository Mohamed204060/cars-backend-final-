"""
order_api.py — طبقة REST API لخدمة الطلبات وعروض الأسعار (PUR)
المرجع: Orders/Messaging/Notifications Contract Extension؛ REQ-PUR-001..018

نموذج الصلاحيات: ملكية فعلية لا دور — المشتري (buyer_user_ref_id) لإلغاء/
قبول طلبه هو، والبائع (عبر متجره المشتق من الجلسة، كما في Inventory) لتقديم/
سحب عروضه هو. لا فحص دور إداري في أي عملية هنا.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import get_pct_repository
from store_api import get_store_repository
from session_service import Session
from order_service import (
    DuplicateActiveOfferError,
    InvalidPurchaseRequestStatusError,
    OfferNotWithdrawableError,
    PurchaseRequestClosedError,
    accept_offer_via_repository,
    cancel_purchase_request_via_repository,
    create_purchase_request_via_repository,
    submit_offer_via_repository,
    withdraw_offer_via_repository,
)

router = APIRouter(prefix="/api/v1", tags=["orders"])


class PurchaseRequestCreateRequest(BaseModel):
    catalog_part_ref_id: str
    trim_ref_id: str


class PurchaseRequestResponse(BaseModel):
    id: str
    business_code: Optional[str] = None
    buyer_user_ref_id: str
    catalog_part_ref_id: str
    trim_ref_id: str
    status: str


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


def get_order_repository(request: Request):
    return request.app.state.order_repository


def _pr_response(pr) -> PurchaseRequestResponse:
    return PurchaseRequestResponse(
        id=pr.id, business_code=pr.business_code, buyer_user_ref_id=pr.buyer_user_ref_id,
        catalog_part_ref_id=pr.catalog_part_ref_id, trim_ref_id=pr.trim_ref_id, status=pr.status,
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
):
    try:
        pr = create_purchase_request_via_repository(
            order_repo, buyer_user_ref_id=current_session.user_id,
            catalog_part_ref_id=body.catalog_part_ref_id, trim_ref_id=body.trim_ref_id,
            is_part_approved_checker=pct_repo.is_part_approved,
        )
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "PART_NOT_APPROVED", str(exc))
    return _pr_response(pr)


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
