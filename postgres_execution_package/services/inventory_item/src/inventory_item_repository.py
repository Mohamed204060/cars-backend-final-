"""
inventory_item_repository.py — طبقة الوصول للبيانات لخدمة عنصر مخزون البائع
المرجع: دليل حوكمة التنفيذ v1.4؛ 009_str.sql (str.inventory_items)

مبدأ لا حذف فعلي مطبَّق هنا أيضًا: لا توجد ولن تُضاف دالة delete_item في
هذا المستودع؛ الإزالة الوحيدة الممكنة هي عبر update_item بحالة "archived"،
تمامًا كما في inventory_item_service.py.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
import uuid

from inventory_item_service import InventoryItem, InventoryItemPublicDetailView


class InventoryItemRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه inventory_item_service.py. لا دالة حذف عمدًا."""

    @abstractmethod
    def insert_item(self, item: InventoryItem) -> InventoryItem:
        raise NotImplementedError

    @abstractmethod
    def get_item_by_id(self, item_id: str) -> Optional[InventoryItem]:
        raise NotImplementedError

    @abstractmethod
    def update_item(self, item: InventoryItem) -> InventoryItem:
        raise NotImplementedError

    @abstractmethod
    def list_items_for_store(self, store_id: str) -> List[InventoryItem]:
        raise NotImplementedError

    # -----------------------------------------------------------------
    # CR-015: طريقة قائمة جديدة مخصَّصة — بلا تعديل على list_items_for_store
    # أعلاه (غير مستخدَمة في أي مكان آخر حاليًا فعليًا، لكن إبقاؤها كما هي
    # حفاظًا على العقد الصريح للطبقة المجرَّدة).
    # -----------------------------------------------------------------

    @abstractmethod
    def list_items_for_store_paginated(self, store_id: str, status: Optional[str],
                                        page: int, page_size: int) -> "tuple[List[InventoryItem], int]":
        raise NotImplementedError

    @abstractmethod
    def list_public_items_for_store_paginated(self, store_id: str, page: int,
                                               page_size: int) -> "tuple[List[InventoryItem], int]":
        """يستبعد دومًا حالتَي hidden وarchived على مستوى الاستعلام نفسه — لا بعد الجلب."""
        raise NotImplementedError

    @abstractmethod
    def get_public_detail(self, item_id: str) -> Optional[InventoryItemPublicDetailView]:
        """
        CR-019: دالة مخصَّصة منفصلة تمامًا عن get_item_by_id (لا تغيير على
        دلالتها أو الاستعلام الذي يخدم مسار المالك). تُنفِّذ JOIN لاسم
        القطعة (نمط search_repository.py حرفيًا) — لا store_name، لا صورة.
        تُعيد None فقط لعدم الوجود؛ فحص حالة hidden/archived مسؤولية طبقة
        الخدمة (get_public_item_detail_via_repository)، لا هنا.
        """
        raise NotImplementedError


class PostgresInventoryItemRepository(InventoryItemRepository):
    """
    تنفيذ فعلي عبر PostgreSQL وفق مخطط str.inventory_items (009_str.sql).
    ملاحظة أمانة: لم يُختبَر على اتصال حي داخل هذه البيئة.
    """

    def __init__(self, connection):
        self._connection = connection

    def insert_item(self, item: InventoryItem) -> InventoryItem:
        query = """
            INSERT INTO str.inventory_items
                (business_code, store_id, catalog_part_ref_id, condition_ref_id, pricing_mode,
                 price_amount, price_currency, quantity, status)
            VALUES (%(business_code)s, %(store_id)s, %(catalog_part_ref_id)s, %(condition_ref_id)s, %(pricing_mode)s,
                    %(price_amount)s, %(price_currency)s, %(quantity)s, %(status)s)
            RETURNING id, business_code
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {
                "business_code": f"IT-{uuid.uuid4().hex[:29]}",
                "store_id": item.store_id, "catalog_part_ref_id": item.catalog_part_ref_id,
                "condition_ref_id": item.condition_ref_id, "pricing_mode": item.pricing_mode,
                "price_amount": item.price_amount, "price_currency": item.price_currency,
                "quantity": item.quantity, "status": item.status,
            })
            row = cur.fetchone()
            item.id = row["id"]
            item.business_code = row["business_code"]
        return item

    def get_item_by_id(self, item_id: str) -> Optional[InventoryItem]:
        # يعتمد على المفتاح الأساسي
        query = """
            SELECT id, business_code, store_id, catalog_part_ref_id, condition_ref_id, pricing_mode,
                   price_amount, price_currency, quantity, status
            FROM str.inventory_items WHERE id = %(id)s
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"id": item_id})
            row = cur.fetchone()
        return self._row_to_item(row) if row else None

    def update_item(self, item: InventoryItem) -> InventoryItem:
        # ملاحظة: هذا التحديث يشمل أيضًا حالة "archived" (الإزالة المنطقية)؛
        # لا استعلام DELETE في هذا الملف بأي حال.
        query = """
            UPDATE str.inventory_items
            SET pricing_mode = %(pricing_mode)s, price_amount = %(price_amount)s,
                price_currency = %(price_currency)s, quantity = %(quantity)s,
                status = %(status)s, updated_at = now()
            WHERE id = %(id)s
        """
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(query, {
                    "id": item.id, "pricing_mode": item.pricing_mode, "price_amount": item.price_amount,
                    "price_currency": item.price_currency, "quantity": item.quantity, "status": item.status,
                })
        return item

    def list_items_for_store(self, store_id: str) -> List[InventoryItem]:
        # يعتمد على idx_inventory_items_store_id
        query = """
            SELECT id, business_code, store_id, catalog_part_ref_id, condition_ref_id, pricing_mode,
                   price_amount, price_currency, quantity, status
            FROM str.inventory_items WHERE store_id = %(store_id)s
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"store_id": store_id})
            rows = cur.fetchall()
        return [self._row_to_item(r) for r in rows]

    def list_items_for_store_paginated(self, store_id: str, status: Optional[str], page: int, page_size: int):
        offset = (page - 1) * page_size
        filters = ["store_id = %(store_id)s"]
        params = {"store_id": store_id, "limit": page_size, "offset": offset}
        if status is not None:
            filters.append("status = %(status)s")
            params["status"] = status
        where_clause = " AND ".join(filters)
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM str.inventory_items WHERE {where_clause}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT id, business_code, store_id, catalog_part_ref_id, condition_ref_id, pricing_mode, "
                f"price_amount, price_currency, quantity, status FROM str.inventory_items "
                f"WHERE {where_clause} ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        return [self._row_to_item(r) for r in rows], total

    def list_public_items_for_store_paginated(self, store_id: str, page: int, page_size: int):
        offset = (page - 1) * page_size
        params = {"store_id": store_id, "limit": page_size, "offset": offset}
        where_clause = "store_id = %(store_id)s AND status IN ('active', 'out_of_stock')"
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM str.inventory_items WHERE {where_clause}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT id, business_code, store_id, catalog_part_ref_id, condition_ref_id, pricing_mode, "
                f"price_amount, price_currency, quantity, status FROM str.inventory_items "
                f"WHERE {where_clause} ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        return [self._row_to_item(r) for r in rows], total

    def get_public_detail(self, item_id: str) -> Optional[InventoryItemPublicDetailView]:
        # LEFT JOIN عمدًا للاثنين (لا JOIN عادي): غياب اسم قطعة أو Ref Value
        # لا ينبغي أن يُسقِط العنصر بالكامل من الظهور العام — نُعيد None لهذا
        # الحقل تحديدًا فقط (يُعالَج في الواجهة، لا افتراض هنا).
        query = """
            SELECT ii.id, ii.store_id, ii.catalog_part_ref_id, ii.condition_ref_id,
                   ii.pricing_mode, ii.price_amount, ii.price_currency, ii.status,
                   pl.name_value AS part_name, rv.code AS condition_code
            FROM str.inventory_items ii
            LEFT JOIN pct.localized_names pl
                ON pl.catalog_part_id = ii.catalog_part_ref_id AND pl.name_kind = 'canonical'
            LEFT JOIN ref.ref_values rv ON rv.id = ii.condition_ref_id
            WHERE ii.id = %(id)s
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"id": item_id})
            row = cur.fetchone()
        if row is None:
            return None
        return InventoryItemPublicDetailView(
            id=row["id"], store_id=row["store_id"], catalog_part_ref_id=row["catalog_part_ref_id"],
            condition_ref_id=row["condition_ref_id"], part_name=row["part_name"],
            condition_code=row["condition_code"], pricing_mode=row["pricing_mode"],
            price_amount=row["price_amount"], price_currency=row["price_currency"], status=row["status"],
        )

    @staticmethod
    def _row_to_item(row) -> InventoryItem:
        return InventoryItem(
            id=row["id"], business_code=row["business_code"], store_id=row["store_id"],
            catalog_part_ref_id=row["catalog_part_ref_id"],
            condition_ref_id=row["condition_ref_id"], pricing_mode=row["pricing_mode"],
            quantity=row["quantity"], price_amount=row["price_amount"],
            price_currency=row["price_currency"], status=row["status"],
        )


class InMemoryInventoryItemRepository(InventoryItemRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._items = {}
        self._next_seq = 1
        # CR-019: يحاكي JOIN إلى pct.localized_names وref.ref_values للاختبار
        # فقط — تُعبَّأ صراحةً عبر set_part_name/set_condition_code أدناه؛
        # غياب مفتاح = نفس سلوك LEFT JOIN الحقيقي (None، لا خطأ).
        self._part_names: dict[str, str] = {}
        self._condition_codes: dict[str, str] = {}

    def set_part_name(self, catalog_part_ref_id: str, name: str) -> None:
        self._part_names[catalog_part_ref_id] = name

    def set_condition_code(self, condition_ref_id: str, code: str) -> None:
        self._condition_codes[condition_ref_id] = code

    def get_public_detail(self, item_id: str) -> Optional[InventoryItemPublicDetailView]:
        item = self._items.get(item_id)
        if item is None:
            return None
        return InventoryItemPublicDetailView(
            id=item.id, store_id=item.store_id, catalog_part_ref_id=item.catalog_part_ref_id,
            condition_ref_id=item.condition_ref_id,
            part_name=self._part_names.get(item.catalog_part_ref_id),
            condition_code=self._condition_codes.get(item.condition_ref_id),
            pricing_mode=item.pricing_mode, price_amount=item.price_amount,
            price_currency=item.price_currency, status=item.status,
        )

    def insert_item(self, item: InventoryItem) -> InventoryItem:
        item.id = f"item-{self._next_seq}"
        item.business_code = f"IT-{self._next_seq:029d}"
        self._next_seq += 1
        self._items[item.id] = item
        return item

    def get_item_by_id(self, item_id: str) -> Optional[InventoryItem]:
        return self._items.get(item_id)

    def update_item(self, item: InventoryItem) -> InventoryItem:
        self._items[item.id] = item
        return item

    def list_items_for_store(self, store_id: str) -> List[InventoryItem]:
        return [i for i in self._items.values() if i.store_id == store_id]

    def list_items_for_store_paginated(self, store_id: str, status: Optional[str], page: int, page_size: int):
        items = [i for i in reversed(list(self._items.values())) if i.store_id == store_id]
        if status is not None:
            items = [i for i in items if i.status == status]
        total = len(items)
        start = (page - 1) * page_size
        return items[start:start + page_size], total

    def list_public_items_for_store_paginated(self, store_id: str, page: int, page_size: int):
        items = [i for i in reversed(list(self._items.values()))
                 if i.store_id == store_id and i.status in ("active", "out_of_stock")]
        total = len(items)
        start = (page - 1) * page_size
        return items[start:start + page_size], total
