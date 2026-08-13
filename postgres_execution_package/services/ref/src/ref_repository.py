"""
ref_repository.py — طبقة الوصول للبيانات لخدمة البيانات المرجعية (REF)
المرجع: دليل حوكمة التنفيذ v1.7؛ 002_ref.sql
"""

import json
from abc import ABC, abstractmethod
from typing import List, Optional

from ref_service import BulkImportJob, BulkImportRowResult, RefValue


class RefRepository(ABC):

    @abstractmethod
    def insert_value(self, value: RefValue) -> RefValue:
        raise NotImplementedError

    @abstractmethod
    def get_value_by_id(self, value_id: str) -> Optional[RefValue]:
        raise NotImplementedError

    @abstractmethod
    def get_values_for_type(self, ref_type: str, include_archived: bool = False) -> List[RefValue]:
        raise NotImplementedError

    @abstractmethod
    def update_value(self, value: RefValue) -> RefValue:
        raise NotImplementedError

    @abstractmethod
    def insert_bulk_import_job(self, job: BulkImportJob) -> BulkImportJob:
        raise NotImplementedError

    @abstractmethod
    def is_value_of_type(self, value_id: str, ref_type: str) -> bool:
        """
        CR-022: نقطة التكامل الرسمية للتحقق من أن معرّفًا مرجعيًا يشير فعليًا
        إلى قيمة **نشطة** (status='active'، REQ-REF-002: أرشفة لا حذف) من
        نوع محدَّد (مثال: part_condition) — لا يكفي وجود UUID في
        ref.ref_values من نوع آخر، ولا قيمة مؤرشَفة. نفس دلالة "active" التي
        يعتمدها get_values_for_type(include_archived=False) افتراضيًا لأي
        استخدام غير استكشافي. تُمرَّر كدالة محقونة (Dependency Injection)
        لخدمات أخرى، بنفس نمط is_part_approved في PCT.
        """
        raise NotImplementedError


class PostgresRefRepository(RefRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 002_ref.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_value(self, value: RefValue) -> RefValue:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ref.ref_values (ref_type, code, status) VALUES (%(ref_type)s, %(code)s, %(status)s) RETURNING id",
                {"ref_type": value.ref_type, "code": value.code, "status": value.status},
            )
            value.id = cur.fetchone()["id"]
        return value

    def get_value_by_id(self, value_id: str) -> Optional[RefValue]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, ref_type, code, status FROM ref.ref_values WHERE id = %(id)s", {"id": value_id})
            row = cur.fetchone()
        return RefValue(id=row["id"], ref_type=row["ref_type"], code=row["code"], status=row["status"]) if row else None

    def get_values_for_type(self, ref_type: str, include_archived: bool = False) -> List[RefValue]:
        query = "SELECT id, ref_type, code, status FROM ref.ref_values WHERE ref_type = %(ref_type)s"
        if not include_archived:
            query += " AND status = 'active'"
        with self._connection.cursor() as cur:
            cur.execute(query, {"ref_type": ref_type})
            rows = cur.fetchall()
        return [RefValue(id=r["id"], ref_type=r["ref_type"], code=r["code"], status=r["status"]) for r in rows]

    def update_value(self, value: RefValue) -> RefValue:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE ref.ref_values SET status = %(status)s, updated_at = now() WHERE id = %(id)s",
                    {"status": value.status, "id": value.id},
                )
        return value

    def insert_bulk_import_job(self, job: BulkImportJob) -> BulkImportJob:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO ref.bulk_import_jobs "
                    "(ref_type, imported_by_ref_id, file_name, status, new_count, updated_count, rejected_count) "
                    "VALUES (%(ref_type)s, %(imported_by)s, %(file_name)s, %(status)s, %(new_count)s, %(updated_count)s, %(rejected_count)s) "
                    "RETURNING id",
                    {"ref_type": job.ref_type, "imported_by": job.imported_by_ref_id, "file_name": job.file_name,
                     "status": job.status, "new_count": job.new_count, "updated_count": job.updated_count,
                     "rejected_count": job.rejected_count},
                )
                job.id = cur.fetchone()["id"]
                for row in job.rows:
                    cur.execute(
                        "INSERT INTO ref.bulk_import_job_rows (job_id, row_number, outcome, rejection_reason, raw_row_data) "
                        "VALUES (%(job_id)s, %(row_number)s, %(outcome)s, %(reason)s, %(raw)s)",
                        {"job_id": job.id, "row_number": row.row_number, "outcome": row.outcome,
                         "reason": row.rejection_reason, "raw": json.dumps(row.raw_row_data)},
                    )
        return job

    def is_value_of_type(self, value_id: str, ref_type: str) -> bool:
        value = self.get_value_by_id(value_id)
        return value is not None and value.ref_type == ref_type and value.status == "active"


class InMemoryRefRepository(RefRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._values = {}
        self._jobs = {}
        self._seq = {"value": 1, "job": 1}

    def insert_value(self, value: RefValue) -> RefValue:
        value.id = f"refvalue-{self._seq['value']}"
        self._seq["value"] += 1
        self._values[value.id] = value
        return value

    def get_value_by_id(self, value_id: str) -> Optional[RefValue]:
        return self._values.get(value_id)

    def get_values_for_type(self, ref_type: str, include_archived: bool = False) -> List[RefValue]:
        return [v for v in self._values.values() if v.ref_type == ref_type and (include_archived or v.status == "active")]

    def update_value(self, value: RefValue) -> RefValue:
        self._values[value.id] = value
        return value

    def insert_bulk_import_job(self, job: BulkImportJob) -> BulkImportJob:
        job.id = f"bulkjob-{self._seq['job']}"
        self._seq["job"] += 1
        self._jobs[job.id] = job
        return job

    def is_value_of_type(self, value_id: str, ref_type: str) -> bool:
        value = self.get_value_by_id(value_id)
        return value is not None and value.ref_type == ref_type and value.status == "active"
