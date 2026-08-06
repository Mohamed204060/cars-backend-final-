"""
inventory_item_api.py — طبقة REST API لعنصر مخزون البائع (STR)
المرجع: Store + Inventory Contract Extension؛ REQ-STR-009..019

نموذج الصلاحيات (REQ-STR-019، يختلف عن كل الخدمات السابقة): لا فحص دور
(Role) هنا إطلاقًا — التعديل مقصور على **البائع المالك فقط** لمتجر العنصر.
يُتحقَّق منه عبر: item.store_id -> store.owner_user_ref_id مقارنةً بمعرّف
الجلسة الحالية، لا عبر iam.users.primary_role. هذا يتطلب حقن StoreRepository
هنا أيضًا (نفس نمط حقن PctRepository/VctRepository في CMP) لجلب المالك
الفعلي دون أي استعلام مباشر عبر خدمة أخرى.

لا فحص ملكية على الإنشاء (POST) نفسه سوى أن يكون store_id المُرسَل مملوكًا
فعليًا لصاحب الجلسة — نفس المبدأ.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import get_pct_repository
from session_service import Session
from store_api import get_store_repository
from idempotency_service import get_cached_response_via_repository, store_response_via_repository
from inventory_item_service import (
    CatalogPartNotApprovedError,
    InvalidPricingError,
    InvalidQuantityError,
    ItemArchivedImmutableError,
    archive_item_via_repository,
    create_inventory_item_via_repository,
    hide_item_via_repository,
    unhide_item_via_repository,
    update_pricing_via_repository,
    update_quantity_via_repository,
)

router = APIRouter(prefix="/api/v1", tags=["inventory"])

CREATE_ITEM_ENDPOINT = "POST /api/v1/inventory-items"


class InventoryItemCreateRequest(BaseModel):
    """
    مطابق حرفيًا للعقد المعتمَد أصلًا (openapi.yaml، الشريحة الأولى): لا
    store_id هنا إطلاقًا — يُشتَق دائمًا من متجر المستخدم الحالي (REQ-STR-001:
    متجر واحد لكل بائع)، لا يُقبَل كمعامل من العميل بأي حال.
    """
    catalog_part_ref_id: str
    condition_ref_id: str
    pricing_mode: str
    quantity: int = 0
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None


class InventoryItemCreateResponse(BaseModel):
    """مطابق حرفيًا لاستجابة العقد المعتمَد أصلًا — 3 حقول فقط، لا الكائن الكامل."""
    id: str
    business_code: str
    status: str


class InventoryItemResponse(BaseModel):
    id: str
    store_id: str
    catalog_part_ref_id: str
    condition_ref_id: str
    pricing_mode: str
    quantity: int
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None
    status: str


class QuantityUpdateRequest(BaseModel):
    new_quantity: int


class PricingUpdateRequest(BaseModel):
    pricing_mode: str
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None


def get_inventory_repository(request: Request):
    return request.app.state.inventory_repository


def get_idempotency_repository(request: Request):
    return request.app.state.idempotency_repository


def _to_response(item) -> InventoryItemResponse:
    return InventoryItemResponse(
        id=item.id, store_id=item.store_id, catalog_part_ref_id=item.catalog_part_ref_id,
        condition_ref_id=item.condition_ref_id, pricing_mode=item.pricing_mode, quantity=item.quantity,
        price_amount=item.price_amount, price_currency=item.price_currency, status=item.status,
    )


def _ensure_owns_store(correlation_id: str, store_repo, store_id: str, user_id: str) -> None:
    """REQ-STR-019/010: يتحقق أن صاحب الجلسة الحالية هو مالك المتجر فعليًا."""
    store = store_repo.get_store_by_id(store_id)
    if store is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "STORE_NOT_FOUND", "المتجر غير موجود.")
    if store.owner_user_ref_id != user_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "هذه العملية مقصورة على البائع المالك للمتجر فقط.")


def _ensure_owns_item(correlation_id: str, store_repo, item, user_id: str) -> None:
    _ensure_owns_store(correlation_id, store_repo, item.store_id, user_id)


@router.post("/inventory-items", response_model=InventoryItemCreateResponse, status_code=status.HTTP_201_CREATED)
def create_item(
    body: InventoryItemCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    inventory_repo=Depends(get_inventory_repository),
    store_repo=Depends(get_store_repository),
    pct_repo=Depends(get_pct_repository),
    idempotency_repo=Depends(get_idempotency_repository),
):
    """
    DD الحزمة 2، القسم 2.2: Idempotency-Key مطلوب (required) لهذه العملية
    تحديدًا. نفس المفتاح لنفس المستخدم يُعيد النتيجة الأصلية دون تنفيذ ثانٍ.
    """
    cached = get_cached_response_via_repository(
        idempotency_repo, idempotency_key, current_session.user_id, CREATE_ITEM_ENDPOINT
    )
    if cached is not None:
        return InventoryItemCreateResponse(**cached.response_body)

    # REQ-STR-001: متجر واحد لكل بائع، يُشتَق من الجلسة لا من الطلب.
    # 403 يشمل: لا متجر أصلاً، أو المتجر غير نشط، أو (نظريًا) غير مملوك.
    store = store_repo.get_store_by_owner_id(current_session.user_id)
    if store is None or store.status != "active":
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "STORE_NOT_ACTIVE_OR_NOT_OWNED",
                    "لا يوجد متجر نشط مملوك لهذا المستخدم.")

    try:
        item = create_inventory_item_via_repository(
            inventory_repo, store_id=store.id, catalog_part_ref_id=body.catalog_part_ref_id,
            condition_ref_id=body.condition_ref_id, pricing_mode=body.pricing_mode, quantity=body.quantity,
            price_amount=body.price_amount, price_currency=body.price_currency,
            is_part_approved_checker=pct_repo.is_part_approved,
        )
    except InvalidPricingError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_PRICING", str(exc))
    except InvalidQuantityError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_QUANTITY", str(exc))
    except CatalogPartNotApprovedError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "PART_NOT_APPROVED", str(exc))

    response_body = {"id": item.id, "business_code": item.business_code, "status": item.status}
    store_response_via_repository(
        idempotency_repo, idempotency_key, current_session.user_id, CREATE_ITEM_ENDPOINT,
        status.HTTP_201_CREATED, response_body,
    )
    return InventoryItemCreateResponse(**response_body)


@router.get("/inventory/items/{item_id}", response_model=InventoryItemResponse)
def get_item(
    item_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    inventory_repo=Depends(get_inventory_repository),
):
    item = inventory_repo.get_item_by_id(item_id)
    if item is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ITEM_NOT_FOUND", "عنصر المخزون غير موجود.")
    return _to_response(item)


def _handle_item_mutation_errors(correlation_id, fn):
    try:
        return fn()
    except ValueError as exc:
        if "لا يوجد عنصر مخزون" in str(exc):
            raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ITEM_NOT_FOUND", str(exc))
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST", str(exc))
    except InvalidQuantityError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_QUANTITY", str(exc))
    except InvalidPricingError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_PRICING", str(exc))
    except ItemArchivedImmutableError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "ITEM_ARCHIVED", str(exc))


@router.patch("/inventory/items/{item_id}/quantity", response_model=InventoryItemResponse)
def update_quantity(
    item_id: str,
    body: QuantityUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    inventory_repo=Depends(get_inventory_repository),
    store_repo=Depends(get_store_repository),
):
    existing = inventory_repo.get_item_by_id(item_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ITEM_NOT_FOUND", "عنصر المخزون غير موجود.")
    _ensure_owns_item(correlation_id, store_repo, existing, current_session.user_id)

    item = _handle_item_mutation_errors(
        correlation_id, lambda: update_quantity_via_repository(inventory_repo, item_id, body.new_quantity)
    )
    return _to_response(item)


@router.patch("/inventory/items/{item_id}/pricing", response_model=InventoryItemResponse)
def update_pricing(
    item_id: str,
    body: PricingUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    inventory_repo=Depends(get_inventory_repository),
    store_repo=Depends(get_store_repository),
):
    existing = inventory_repo.get_item_by_id(item_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ITEM_NOT_FOUND", "عنصر المخزون غير موجود.")
    _ensure_owns_item(correlation_id, store_repo, existing, current_session.user_id)

    item = _handle_item_mutation_errors(
        correlation_id,
        lambda: update_pricing_via_repository(inventory_repo, item_id, body.pricing_mode, body.price_amount, body.price_currency),
    )
    return _to_response(item)


@router.post("/inventory/items/{item_id}/hide", response_model=InventoryItemResponse)
def hide_item(
    item_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    inventory_repo=Depends(get_inventory_repository),
    store_repo=Depends(get_store_repository),
):
    existing = inventory_repo.get_item_by_id(item_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ITEM_NOT_FOUND", "عنصر المخزون غير موجود.")
    _ensure_owns_item(correlation_id, store_repo, existing, current_session.user_id)

    item = _handle_item_mutation_errors(correlation_id, lambda: hide_item_via_repository(inventory_repo, item_id))
    return _to_response(item)


@router.post("/inventory/items/{item_id}/unhide", response_model=InventoryItemResponse)
def unhide_item(
    item_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    inventory_repo=Depends(get_inventory_repository),
    store_repo=Depends(get_store_repository),
):
    existing = inventory_repo.get_item_by_id(item_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ITEM_NOT_FOUND", "عنصر المخزون غير موجود.")
    _ensure_owns_item(correlation_id, store_repo, existing, current_session.user_id)

    item = _handle_item_mutation_errors(correlation_id, lambda: unhide_item_via_repository(inventory_repo, item_id))
    return _to_response(item)


@router.post("/inventory/items/{item_id}/archive", response_model=InventoryItemResponse)
def archive_item(
    item_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    inventory_repo=Depends(get_inventory_repository),
    store_repo=Depends(get_store_repository),
):
    existing = inventory_repo.get_item_by_id(item_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ITEM_NOT_FOUND", "عنصر المخزون غير موجود.")
    _ensure_owns_item(correlation_id, store_repo, existing, current_session.user_id)

    item = _handle_item_mutation_errors(correlation_id, lambda: archive_item_via_repository(inventory_repo, item_id))
    return _to_response(item)
