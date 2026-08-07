"""
store_repository.py — طبقة الوصول للبيانات لخدمة المتاجر (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.4 (معيار Repository الإلزامي + Repository DoD
        + سلسلة الاعتماد API -> Service -> Repository)؛ 009_str.sql، 017_add_store_location.sql

نفس بنية search_repository.py وauth_repository.py تمامًا. مبدأ SSOT مطبَّق
حرفيًا هنا: لا استعلام في هذا الملف يجلب أي بيانات من جداول iam (الهوية)؛
owner_user_ref_id يُستهلَك دائمًا كمعرّف مرجعي مجرَّد.
"""

from abc import ABC, abstractmethod
from typing import Optional

from store_service import Store


class StoreRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه store_service.py."""

    @abstractmethod
    def insert_store(self, store: Store) -> Store:
        raise NotImplementedError

    @abstractmethod
    def get_store_by_id(self, store_id: str) -> Optional[Store]:
        raise NotImplementedError

    @abstractmethod
    def update_store(self, store: Store) -> Store:
        raise NotImplementedError


class PostgresStoreRepository(StoreRepository):
    """
    تنفيذ فعلي عبر PostgreSQL وفق مخطط str.stores (009_str.sql + 017_add_store_location.sql).
    ملاحظة أمانة: لم يُختبَر على اتصال حي داخل هذه البيئة.
    """

    def __init__(self, connection):
        self._connection = connection

    def insert_store(self, store: Store) -> Store:
        # REQ-STR-001: الإنشاء التلقائي؛ عملية إدراج واحدة، لا تستوجب معاملة متعددة الخطوات هنا
        query = """
            INSERT INTO str.stores (owner_user_ref_id, status, country_ref_id, city_ref_id)
            VALUES (%(owner_user_ref_id)s, %(status)s, %(country_ref_id)s, %(city_ref_id)s)
            RETURNING id
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {
                "owner_user_ref_id": store.owner_user_ref_id, "status": store.status,
                "country_ref_id": store.country_ref_id, "city_ref_id": store.city_ref_id,
            })
            store.id = cur.fetchone()["id"]
        return store

    def get_store_by_id(self, store_id: str) -> Optional[Store]:
        # يعتمد على المفتاح الأساسي (فهرس ضمني)
        query = "SELECT id, owner_user_ref_id, status, country_ref_id, city_ref_id FROM str.stores WHERE id = %(id)s"
        with self._connection.cursor() as cur:
            cur.execute(query, {"id": store_id})
            row = cur.fetchone()
        if row is None:
            return None
        return Store(id=row["id"], owner_user_ref_id=row["owner_user_ref_id"], status=row["status"],
                     country_ref_id=row["country_ref_id"], city_ref_id=row["city_ref_id"])

    def update_store(self, store: Store) -> Store:
        # REQ-STR-006: تحديث المالك يمر عبر هذه الدالة نفسها (لا استعلام منفصل)؛
        # التحقق من صلاحية actor_role يتم في store_service.py قبل الوصول هنا
        query = """
            UPDATE str.stores
            SET owner_user_ref_id = %(owner_user_ref_id)s, status = %(status)s,
                country_ref_id = %(country_ref_id)s, city_ref_id = %(city_ref_id)s,
                updated_at = now()
            WHERE id = %(id)s
        """
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(query, {
                    "id": store.id, "owner_user_ref_id": store.owner_user_ref_id,
                    "status": store.status, "country_ref_id": store.country_ref_id,
                    "city_ref_id": store.city_ref_id,
                })
        return store


class InMemoryStoreRepository(StoreRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._stores = {}
        self._next_seq = 1

    def insert_store(self, store: Store) -> Store:
        store.id = f"store-{self._next_seq}"
        self._next_seq += 1
        self._stores[store.id] = store
        return store

    def get_store_by_id(self, store_id: str) -> Optional[Store]:
        return self._stores.get(store_id)

    def update_store(self, store: Store) -> Store:
        self._stores[store.id] = store
        return store
