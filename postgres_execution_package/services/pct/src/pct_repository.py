"""
pct_repository.py — طبقة الوصول للبيانات لخدمة كتالوج قطع الغيار (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.4؛ 006_pct.sql
"""

from abc import ABC, abstractmethod
from typing import Optional, List

from pct_service import CatalogPart, LocalizedName, OemNumber


class PctRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه pct_service.py."""

    @abstractmethod
    def insert_part(self, part: CatalogPart) -> CatalogPart:
        raise NotImplementedError

    @abstractmethod
    def get_part_by_id(self, part_id: str) -> Optional[CatalogPart]:
        raise NotImplementedError

    @abstractmethod
    def update_part(self, part: CatalogPart) -> CatalogPart:
        raise NotImplementedError

    @abstractmethod
    def insert_localized_name(self, name: LocalizedName) -> LocalizedName:
        raise NotImplementedError

    @abstractmethod
    def get_oem_numbers_for_manufacturer(self, manufacturer_ref_id: str) -> List[OemNumber]:
        raise NotImplementedError

    @abstractmethod
    def insert_oem_number(self, oem: OemNumber) -> OemNumber:
        raise NotImplementedError

    @abstractmethod
    def is_part_approved(self, part_id: str) -> bool:
        """
        نقطة التكامل الرسمية الوحيدة التي تستخدمها خدمات أخرى (كعنصر
        المخزون STR) للتحقق من اعتماد قطعة كتالوج، دون أي وصول مباشر لبيانات
        PCT الداخلية؛ تُمرَّر كدالة محقونة (Dependency Injection) لخدمة STR.
        """
        raise NotImplementedError


class PostgresPctRepository(PctRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 006_pct.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_part(self, part: CatalogPart) -> CatalogPart:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pct.catalog_parts (category_id, status) VALUES (%(category_id)s, %(status)s) RETURNING id",
                {"category_id": part.category_id, "status": part.status},
            )
            part.id = cur.fetchone()["id"]
        return part

    def get_part_by_id(self, part_id: str) -> Optional[CatalogPart]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, category_id, status FROM pct.catalog_parts WHERE id = %(id)s", {"id": part_id})
            row = cur.fetchone()
        return CatalogPart(id=row["id"], category_id=row["category_id"], status=row["status"]) if row else None

    def update_part(self, part: CatalogPart) -> CatalogPart:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE pct.catalog_parts SET status = %(status)s, updated_at = now() WHERE id = %(id)s",
                    {"status": part.status, "id": part.id},
                )
        return part

    def insert_localized_name(self, name: LocalizedName) -> LocalizedName:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pct.localized_names (catalog_part_id, locale, name_value, name_kind) "
                "VALUES (%(catalog_part_id)s, %(locale)s, %(name_value)s, %(name_kind)s) RETURNING id",
                {"catalog_part_id": name.catalog_part_id, "locale": name.locale,
                 "name_value": name.name_value, "name_kind": name.name_kind},
            )
            name.id = cur.fetchone()["id"]
        return name

    def get_oem_numbers_for_manufacturer(self, manufacturer_ref_id: str) -> List[OemNumber]:
        # يعتمد على uq_oem_numbers_manufacturer_number (فهرس تفرّد يُستخدَم كفهرس بحث أيضًا)
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, catalog_part_id, manufacturer_ref_id, oem_number FROM pct.oem_numbers "
                "WHERE manufacturer_ref_id = %(manufacturer_ref_id)s",
                {"manufacturer_ref_id": manufacturer_ref_id},
            )
            rows = cur.fetchall()
        return [OemNumber(id=r["id"], catalog_part_id=r["catalog_part_id"],
                           manufacturer_ref_id=r["manufacturer_ref_id"], oem_number=r["oem_number"]) for r in rows]

    def insert_oem_number(self, oem: OemNumber) -> OemNumber:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pct.oem_numbers (catalog_part_id, manufacturer_ref_id, oem_number) "
                "VALUES (%(catalog_part_id)s, %(manufacturer_ref_id)s, %(oem_number)s) RETURNING id",
                {"catalog_part_id": oem.catalog_part_id, "manufacturer_ref_id": oem.manufacturer_ref_id,
                 "oem_number": oem.oem_number},
            )
            oem.id = cur.fetchone()["id"]
        return oem

    def is_part_approved(self, part_id: str) -> bool:
        part = self.get_part_by_id(part_id)
        return part is not None and part.status == "approved"


class InMemoryPctRepository(PctRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._parts = {}
        self._names = []
        self._oem_numbers = []
        self._next_part_seq = 1
        self._next_name_seq = 1
        self._next_oem_seq = 1

    def insert_part(self, part: CatalogPart) -> CatalogPart:
        part.id = f"part-{self._next_part_seq}"
        self._next_part_seq += 1
        self._parts[part.id] = part
        return part

    def get_part_by_id(self, part_id: str) -> Optional[CatalogPart]:
        return self._parts.get(part_id)

    def update_part(self, part: CatalogPart) -> CatalogPart:
        self._parts[part.id] = part
        return part

    def insert_localized_name(self, name: LocalizedName) -> LocalizedName:
        name.id = f"name-{self._next_name_seq}"
        self._next_name_seq += 1
        self._names.append(name)
        return name

    def get_oem_numbers_for_manufacturer(self, manufacturer_ref_id: str) -> List[OemNumber]:
        return [o for o in self._oem_numbers if o.manufacturer_ref_id == manufacturer_ref_id]

    def insert_oem_number(self, oem: OemNumber) -> OemNumber:
        oem.id = f"oem-{self._next_oem_seq}"
        self._next_oem_seq += 1
        self._oem_numbers.append(oem)
        return oem

    def is_part_approved(self, part_id: str) -> bool:
        part = self._parts.get(part_id)
        return part is not None and part.status == "approved"
