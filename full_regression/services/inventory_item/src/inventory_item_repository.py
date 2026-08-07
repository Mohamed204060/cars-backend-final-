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

from inventory_item_service import InventoryItem


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
            RETURNING id
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {
                "business_code": f"IT-{uuid.uuid4().hex[:29]}",
                "store_id": item.store_id, "catalog_part_ref_id": item.catalog_part_ref_id,
                "condition_ref_id": item.condition_ref_id, "pricing_mode": item.pricing_mode,
                "price_amount": item.price_amount, "price_currency": item.price_currency,
                "quantity": item.quantity, "status": item.status,
            })
            item.id = cur.fetchone()["id"]
        return item

    def get_item_by_id(self, item_id: str) -> Optional[InventoryItem]:
        # يعتمد على المفتاح الأساسي
        query = """
            SELECT id, store_id, catalog_part_ref_id, condition_ref_id, pricing_mode,
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
            SELECT id, store_id, catalog_part_ref_id, condition_ref_id, pricing_mode,
                   price_amount, price_currency, quantity, status
            FROM str.inventory_items WHERE store_id = %(store_id)s
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"store_id": store_id})
            rows = cur.fetchall()
        return [self._row_to_item(r) for r in rows]

    @staticmethod
    def _row_to_item(row) -> InventoryItem:
        return InventoryItem(
            id=row["id"], store_id=row["store_id"], catalog_part_ref_id=row["catalog_part_ref_id"],
            condition_ref_id=row["condition_ref_id"], pricing_mode=row["pricing_mode"],
            quantity=row["quantity"], price_amount=row["price_amount"],
            price_currency=row["price_currency"], status=row["status"],
        )


class InMemoryInventoryItemRepository(InventoryItemRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._items = {}
        self._next_seq = 1

    def insert_item(self, item: InventoryItem) -> InventoryItem:
        item.id = f"item-{self._next_seq}"
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
