"""
inventory_item_service.py — منطق خدمة عنصر مخزون البائع (STR)
المرجع: REQ-STR-009..025 (يشمل سياسة التسعير والصور والحذف المنطقي)؛
        مبدأ SSOT: عنصر المخزون يشير لقطعة الكتالوج بمعرّف مرجعي فقط
        (catalog_part_ref_id) دون نسخ بياناتها.

مبدأ إضافي معتمَد صراحة لهذه الخدمة: لا حذف فعلي (Hard Delete) لعنصر
مخزون على الإطلاق؛ الإزالة تتم حصرًا عبر تغيير الحالة إلى "مؤرشف"
(Soft Delete)، حفاظًا على سلامة طلبات الشراء التاريخية وسجل التدقيق.
لذلك لا توجد أي دالة "حذف" في هذا الملف عمدًا.
"""

from dataclasses import dataclass
from typing import Optional


VALID_STATUSES = {"active", "out_of_stock", "hidden", "archived"}
VALID_PRICING_MODES = {"fixed_price", "contact_for_price"}


@dataclass
class InventoryItem:
    id: str
    store_id: str
    catalog_part_ref_id: str  # SSOT: إشارة مرجعية فقط لخدمة الكتالوج (PCT)، لا نسخ بياناتها
    condition_ref_id: str     # SSOT: إشارة مرجعية لخدمة البيانات المرجعية (REF)
    pricing_mode: str
    quantity: int = 0
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None
    status: str = "active"
    primary_photo_ref: Optional[str] = None
    business_code: Optional[str] = None  # REQ-STR-018: معرّف أعمال ظاهر؛ يُسنَد عبر Repository


class InvalidPricingError(Exception):
    """REQ-STR-012: تعارض بين سياسة التسعير والحقول المرفَقة."""


class ItemArchivedImmutableError(Exception):
    """REQ-STR-019: لا تعديل مسموح على عنصر مؤرشف."""


class InvalidQuantityError(Exception):
    """الكمية يجب ألا تكون سالبة."""


class CatalogPartNotApprovedError(Exception):
    """
    مبدأ SSOT المعتمَد صراحة: لا يجوز إنشاء عنصر مخزون إلا بالإشارة لقطعة
    كتالوج معتمدة عبر مرجع رسمي (catalog_part_ref_id)؛ لا اعتماد على أسماء
    نصية أو بيانات مكرَّرة لتحديد القطعة (REQ-STR-009).
    """


# ---------------------------------------------------------------------------
# REQ-STR-012: التحقق من اتساق سياسة التسعير
# ---------------------------------------------------------------------------

def validate_pricing(pricing_mode: str, price_amount: Optional[float], price_currency: Optional[str]) -> None:
    if pricing_mode not in VALID_PRICING_MODES:
        raise InvalidPricingError(f"سياسة تسعير غير معروفة: {pricing_mode}")

    if pricing_mode == "fixed_price":
        if price_amount is None or price_currency is None:
            raise InvalidPricingError("سياسة \"سعر محدد\" تستوجب مبلغًا وعملة معًا.")
        if price_amount < 0:
            raise InvalidPricingError("لا يجوز أن يكون المبلغ سالبًا.")
    else:  # contact_for_price
        if price_amount is not None:
            raise InvalidPricingError("سياسة \"السعر عند التواصل\" لا تقبل مبلغًا مرفَقًا.")


# ---------------------------------------------------------------------------
# REQ-STR-009..017: إنشاء عنصر المخزون
# ---------------------------------------------------------------------------

def create_inventory_item(
    store_id: str,
    catalog_part_ref_id: str,
    condition_ref_id: str,
    pricing_mode: str,
    quantity: int = 0,
    price_amount: Optional[float] = None,
    price_currency: Optional[str] = None,
    is_part_approved_checker=None,  # Callable[[str], bool] محقونة من خدمة PCT؛ لا استعلام مباشر هنا (SSOT)
) -> InventoryItem:
    if quantity < 0:
        raise InvalidQuantityError("الكمية الابتدائية يجب ألا تكون سالبة.")
    validate_pricing(pricing_mode, price_amount, price_currency)

    # مبدأ SSOT المعتمَد: التحقق من الاعتماد يتم فقط عبر الدالة المحقونة
    # (is_part_approved_checker) القادمة من خدمة PCT؛ إن غابت، لا يُفترَض الاعتماد
    # ضمنًا — يُترَك التحقق لطبقة التنسيق الأعلى (API) إن لم تُمرَّر هنا.
    if is_part_approved_checker is not None and not is_part_approved_checker(catalog_part_ref_id):
        raise CatalogPartNotApprovedError(
            f"قطعة الكتالوج المرجعية '{catalog_part_ref_id}' غير معتمدة أو غير موجودة."
        )

    initial_status = "active" if quantity > 0 else "out_of_stock"  # REQ-STR-017

    return InventoryItem(
        id="",  # يُسنَد فعليًا عبر Repository
        store_id=store_id,
        catalog_part_ref_id=catalog_part_ref_id,
        condition_ref_id=condition_ref_id,
        pricing_mode=pricing_mode,
        quantity=quantity,
        price_amount=price_amount,
        price_currency=price_currency,
        status=initial_status,
    )


# ---------------------------------------------------------------------------
# قيد التعديل: لا تعديل بعد الأرشفة (REQ-STR-019)
# ---------------------------------------------------------------------------

def ensure_modifiable(item: InventoryItem) -> None:
    if item.status == "archived":
        raise ItemArchivedImmutableError(
            "لا يجوز تعديل عنصر مخزون مؤرشف بأي حال."
        )


# ---------------------------------------------------------------------------
# REQ-STR-017: تحديث الكمية مع الانتقال التلقائي لحالة نفاد المخزون
# ---------------------------------------------------------------------------

def update_quantity(item: InventoryItem, new_quantity: int) -> InventoryItem:
    ensure_modifiable(item)
    if new_quantity < 0:
        raise InvalidQuantityError("الكمية يجب ألا تكون سالبة.")

    item.quantity = new_quantity

    # الانتقال التلقائي بين active وout_of_stock فقط؛ لا يمسّ hidden
    if item.status in {"active", "out_of_stock"}:
        item.status = "active" if new_quantity > 0 else "out_of_stock"

    return item


# ---------------------------------------------------------------------------
# REQ-STR-012: تحديث سياسة التسعير
# ---------------------------------------------------------------------------

def update_pricing(item: InventoryItem, pricing_mode: str,
                    price_amount: Optional[float] = None, price_currency: Optional[str] = None) -> InventoryItem:
    ensure_modifiable(item)
    validate_pricing(pricing_mode, price_amount, price_currency)
    item.pricing_mode = pricing_mode
    item.price_amount = price_amount
    item.price_currency = price_currency
    return item


# ---------------------------------------------------------------------------
# REQ-STR-017: إخفاء/إظهار العنصر (لا حذف)
# ---------------------------------------------------------------------------

def hide_item(item: InventoryItem) -> InventoryItem:
    ensure_modifiable(item)
    item.status = "hidden"
    return item


def unhide_item(item: InventoryItem) -> InventoryItem:
    ensure_modifiable(item)
    if item.status != "hidden":
        raise ValueError("العنصر ليس مخفيًا حاليًا.")
    item.status = "active" if item.quantity > 0 else "out_of_stock"
    return item


# ---------------------------------------------------------------------------
# الإزالة الوحيدة المسموحة: الأرشفة (Soft Delete) — لا حذف فعلي إطلاقًا
# ---------------------------------------------------------------------------

def archive_item(item: InventoryItem) -> InventoryItem:
    """
    مبدأ معتمَد صراحة: هذه هي الطريقة الوحيدة لـ"إزالة" عنصر مخزون في كامل
    هذا الملف؛ لا توجد ولن تُضاف أي دالة حذف فعلي (DELETE) لعناصر المخزون،
    حفاظًا على سلامة طلبات الشراء التاريخية وسجل التدقيق المرتبط بالعنصر.
    """
    ensure_modifiable(item)  # عنصر مؤرشف بالفعل لا يُعاد أرشفته
    item.status = "archived"
    return item


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4)
# لا دالة "حذف عبر Repository" هنا أيضًا؛ فقط archive_via_repository (أرشفة).
# ---------------------------------------------------------------------------

def create_inventory_item_via_repository(
    repository, store_id: str, catalog_part_ref_id: str, condition_ref_id: str,
    pricing_mode: str, quantity: int = 0,
    price_amount: Optional[float] = None, price_currency: Optional[str] = None,
    is_part_approved_checker=None,
) -> InventoryItem:
    item = create_inventory_item(store_id, catalog_part_ref_id, condition_ref_id,
                                  pricing_mode, quantity, price_amount, price_currency,
                                  is_part_approved_checker=is_part_approved_checker)
    return repository.insert_item(item)


def update_quantity_via_repository(repository, item_id: str, new_quantity: int) -> InventoryItem:
    item = repository.get_item_by_id(item_id)
    if item is None:
        raise ValueError(f"لا يوجد عنصر مخزون بالمعرّف: {item_id}")
    update_quantity(item, new_quantity)
    return repository.update_item(item)


def update_pricing_via_repository(
    repository, item_id: str, pricing_mode: str,
    price_amount: Optional[float] = None, price_currency: Optional[str] = None,
) -> InventoryItem:
    """امتداد Store+Inventory Contract Extension: كان الغلاف مفقودًا رغم وجود update_pricing النقية."""
    item = repository.get_item_by_id(item_id)
    if item is None:
        raise ValueError(f"لا يوجد عنصر مخزون بالمعرّف: {item_id}")
    update_pricing(item, pricing_mode, price_amount, price_currency)
    return repository.update_item(item)


def hide_item_via_repository(repository, item_id: str) -> InventoryItem:
    item = repository.get_item_by_id(item_id)
    if item is None:
        raise ValueError(f"لا يوجد عنصر مخزون بالمعرّف: {item_id}")
    hide_item(item)
    return repository.update_item(item)


def unhide_item_via_repository(repository, item_id: str) -> InventoryItem:
    item = repository.get_item_by_id(item_id)
    if item is None:
        raise ValueError(f"لا يوجد عنصر مخزون بالمعرّف: {item_id}")
    unhide_item(item)
    return repository.update_item(item)


def archive_item_via_repository(repository, item_id: str) -> InventoryItem:
    """الإزالة المنطقية الوحيدة المتاحة عبر Repository؛ لا استدعاء حذف فعلي أبدًا."""
    item = repository.get_item_by_id(item_id)
    if item is None:
        raise ValueError(f"لا يوجد عنصر مخزون بالمعرّف: {item_id}")
    archive_item(item)
    return repository.update_item(item)


# ---------------------------------------------------------------------------
# CR-015: قوائم Frontend Enablement — بلا تعديل على أي دالة أعلاه
# ---------------------------------------------------------------------------

def list_my_inventory_items_via_repository(repository, store_id: str, status: Optional[str],
                                            page: int, page_size: int):
    """المالك فقط يصل هنا (يُتحقَّق من الملكية في طبقة الـAPI قبل الاستدعاء)؛
    كل الحقول تُعاد بلا تصفية (مخفي/مؤرشَف مشمولان — هذه لوحة المالك)."""
    return repository.list_items_for_store_paginated(store_id, status, page, page_size)


def list_public_store_inventory_items_via_repository(repository, store_id: str, page: int, page_size: int):
    """
    عرض عام: لا يُعيد أبدًا عناصر بحالة hidden أو archived — التصفية على
    مستوى الاستعلام نفسه (list_public_items_for_store_paginated)، لا بعد
    الترقيم، لتفادي صفحات ناقصة العدد أو total غير دقيق (نفس المبدأ
    المطبَّق في order_service.py لعروض البائع).
    """
    return repository.list_public_items_for_store_paginated(store_id, page, page_size)
