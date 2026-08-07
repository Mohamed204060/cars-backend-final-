"""
cmp_repository.py — طبقة الوصول للبيانات لخدمة التوافق (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.4؛ 007_cmp.sql
"""

from abc import ABC, abstractmethod
from typing import List

from cmp_service import CompatibilityRecord


class CmpRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه cmp_service.py."""

    @abstractmethod
    def insert_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        raise NotImplementedError

    @abstractmethod
    def get_records_for_part(self, catalog_part_ref_id: str) -> List[CompatibilityRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_record_by_id(self, record_id: str):
        """امتداد CMP Contract Extension: كانت مفقودة رغم وجود insert_record."""
        raise NotImplementedError

    @abstractmethod
    def update_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        raise NotImplementedError


class PostgresCmpRepository(CmpRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 007_cmp.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        # يعتمد على uq_compatibility_part_trim (فهرس تفرّد يُستخدَم أيضًا لفحص التكرار)
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO cmp.compatibility_records (catalog_part_ref_id, trim_ref_id, status) "
                "VALUES (%(catalog_part_ref_id)s, %(trim_ref_id)s, %(status)s) RETURNING id",
                {"catalog_part_ref_id": record.catalog_part_ref_id, "trim_ref_id": record.trim_ref_id,
                 "status": record.status},
            )
            record.id = cur.fetchone()["id"]
        return record

    def get_records_for_part(self, catalog_part_ref_id: str) -> List[CompatibilityRecord]:
        # يعتمد على idx_compatibility_part
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, catalog_part_ref_id, trim_ref_id, status FROM cmp.compatibility_records "
                "WHERE catalog_part_ref_id = %(part_id)s",
                {"part_id": catalog_part_ref_id},
            )
            rows = cur.fetchall()
        return [CompatibilityRecord(id=r["id"], catalog_part_ref_id=r["catalog_part_ref_id"],
                                     trim_ref_id=r["trim_ref_id"], status=r["status"]) for r in rows]

    def get_record_by_id(self, record_id: str):
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, catalog_part_ref_id, trim_ref_id, status FROM cmp.compatibility_records WHERE id = %(id)s",
                {"id": record_id},
            )
            row = cur.fetchone()
        if row is None:
            return None
        return CompatibilityRecord(id=row["id"], catalog_part_ref_id=row["catalog_part_ref_id"],
                                    trim_ref_id=row["trim_ref_id"], status=row["status"])

    def update_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE cmp.compatibility_records SET status = %(status)s, updated_at = now() WHERE id = %(id)s",
                    {"status": record.status, "id": record.id},
                )
        return record


class InMemoryCmpRepository(CmpRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._records = {}
        self._next_seq = 1

    def insert_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        record.id = f"cmp-{self._next_seq}"
        self._next_seq += 1
        self._records[record.id] = record
        return record

    def get_records_for_part(self, catalog_part_ref_id: str) -> List[CompatibilityRecord]:
        return [r for r in self._records.values() if r.catalog_part_ref_id == catalog_part_ref_id]

    def get_record_by_id(self, record_id: str):
        return self._records.get(record_id)

    def update_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        self._records[record.id] = record
        return record
