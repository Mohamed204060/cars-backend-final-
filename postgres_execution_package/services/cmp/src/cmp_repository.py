"""
cmp_repository.py — طبقة الوصول للبيانات لخدمة التوافق (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.4؛ 007_cmp.sql
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from cmp_service import CompatibilityRecord, CompatibilityLevelConflictError
from advisory_lock import compute_advisory_lock_key


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

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §10-17، Batch 1: Year-specific Compatibility
    # -----------------------------------------------------------------

    @abstractmethod
    def get_trim_ref_id_for_trim_model_year(self, trim_model_year_id: str) -> Optional[str]:
        """Resolve عبر نطاق VCT — نفس نمط is_trim_valid_checker المحقون، بلا استعلام VCT مباشر هنا؛
        التنفيذ الفعلي (Postgres) يُدرَك عبر حقن اتصال/دالة VCT عند التركيب (main.py)، لا استيراد VCT من CMP."""
        raise NotImplementedError

    @abstractmethod
    def insert_compatibility_record_with_lock(
        self, catalog_part_ref_id: str, resolved_trim_ref_id: str,
        trim_ref_id: Optional[str], trim_model_year_ref_id: Optional[str],
        fitment_type: str, compatibility_notes: Optional[str], source: str,
    ) -> CompatibilityRecord:
        """
        §16: يحصل على Advisory Lock بنطاق (cmp-compatibility,
        catalog_part_ref_id, resolved_trim_ref_id)، يعيد فحص التعايش
        (General مقابل Year-specific لنفس الزوج، §13) وفحص التكرار
        للهدف الدقيق (§14)، ثم يُدرِج — كل ذلك ضمن نفس Transaction.
        يرفع CompatibilityLevelConflictError عند اكتشاف تعايش،
        وDuplicateCompatibilityRecordError عند تكرار الهدف الدقيق.
        """
        raise NotImplementedError

    @abstractmethod
    def has_general_compatibility(self, catalog_part_ref_id: str, trim_ref_id: str) -> bool:
        """§18-19: نقطة القراءة لـsearch_repository — لا استعلام SQL خام من هناك؛ العقد هنا فقط."""
        raise NotImplementedError

    @abstractmethod
    def has_any_year_specific_compatibility(self, catalog_part_ref_id: str, trim_ref_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def has_year_specific_compatibility_for_year(self, catalog_part_ref_id: str, trim_ref_id: str, year: int) -> bool:
        raise NotImplementedError


class PostgresCmpRepository(CmpRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 007_cmp.sql + 030 (Batch 1)."""

    def __init__(self, connection):
        self._connection = connection

    def insert_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        # ملاحظة: يبقى هذا المسار القديم (General حصرًا، لتوافق الاستدعاءات
        # القديمة) — المسار الفعلي الجديد هو insert_compatibility_record_with_lock.
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
                "SELECT id, catalog_part_ref_id, trim_ref_id, trim_model_year_ref_id, status "
                "FROM cmp.compatibility_records WHERE catalog_part_ref_id = %(part_id)s",
                {"part_id": catalog_part_ref_id},
            )
            rows = cur.fetchall()
        return [CompatibilityRecord(id=r["id"], catalog_part_ref_id=r["catalog_part_ref_id"],
                                     trim_ref_id=r["trim_ref_id"], trim_model_year_ref_id=r["trim_model_year_ref_id"],
                                     status=r["status"]) for r in rows]

    def get_record_by_id(self, record_id: str):
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, catalog_part_ref_id, trim_ref_id, trim_model_year_ref_id, status "
                "FROM cmp.compatibility_records WHERE id = %(id)s",
                {"id": record_id},
            )
            row = cur.fetchone()
        if row is None:
            return None
        return CompatibilityRecord(id=row["id"], catalog_part_ref_id=row["catalog_part_ref_id"],
                                    trim_ref_id=row["trim_ref_id"], trim_model_year_ref_id=row["trim_model_year_ref_id"],
                                    status=row["status"])

    def update_record(self, record: CompatibilityRecord) -> CompatibilityRecord:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE cmp.compatibility_records SET status = %(status)s, updated_at = now() WHERE id = %(id)s",
                    {"status": record.status, "id": record.id},
                )
        return record

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §10-17، Batch 1
    # -----------------------------------------------------------------

    def get_trim_ref_id_for_trim_model_year(self, trim_model_year_id: str) -> Optional[str]:
        # استعلام عابر للنطاق (cmp → vct) على مستوى SQL/Repository فقط — نفس
        # نمط search_repository.py القائم أصلًا (يربط str/pct/cmp معًا)؛
        # مبدأ SSOT يقيِّد طبقة الأعمال (Service) لا طبقة SQL الخام.
        with self._connection.cursor() as cur:
            cur.execute("SELECT trim_ref_id FROM vct.trim_model_years WHERE id = %(id)s", {"id": trim_model_year_id})
            row = cur.fetchone()
        return row["trim_ref_id"] if row else None

    def insert_compatibility_record_with_lock(
        self, catalog_part_ref_id: str, resolved_trim_ref_id: str,
        trim_ref_id: Optional[str], trim_model_year_ref_id: Optional[str],
        fitment_type: str, compatibility_notes: Optional[str], source: str,
    ) -> CompatibilityRecord:
        # ملاحظة نطاق قائمة مسبقًا (007_cmp.sql الأصلي): fitment_type/
        # compatibility_notes/source لا أعمدة فعلية لها في قاعدة البيانات
        # حتى بعد 030 — خارج نطاق Approved VCT Design Baseline لهذه الدفعة
        # عمدًا (تستوجب Migration وقرار حوكمي منفصل). تُقبَل هنا وتُعاد في
        # الكائن Python فقط، بلا تخزين — نفس السلوك القديم حرفيًا، موروث لا مُستحدَث.
        lock_key = compute_advisory_lock_key("cmp-compatibility", catalog_part_ref_id, resolved_trim_ref_id)

        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%(key)s)", {"key": lock_key})

                # §13/§16: إعادة الفحص الفعلي داخل نفس Transaction بعد القفل
                cur.execute(
                    "SELECT COUNT(*) AS c FROM cmp.compatibility_records "
                    "WHERE catalog_part_ref_id = %(part_id)s AND trim_ref_id = %(trim_id)s",
                    {"part_id": catalog_part_ref_id, "trim_id": resolved_trim_ref_id},
                )
                general_count = cur.fetchone()["c"]
                cur.execute(
                    "SELECT COUNT(*) AS c FROM cmp.compatibility_records cr "
                    "JOIN vct.trim_model_years tmy ON tmy.id = cr.trim_model_year_ref_id "
                    "WHERE cr.catalog_part_ref_id = %(part_id)s AND tmy.trim_ref_id = %(trim_id)s",
                    {"part_id": catalog_part_ref_id, "trim_id": resolved_trim_ref_id},
                )
                year_specific_count = cur.fetchone()["c"]

                if trim_ref_id is not None and year_specific_count > 0:
                    raise CompatibilityLevelConflictError(
                        "توجد بالفعل سجلات توافق Year-specific لنفس القطعة/الفئة؛ "
                        "لا يجوز إضافة سجل General لنفس الزوج (§13)."
                    )
                if trim_model_year_ref_id is not None and general_count > 0:
                    raise CompatibilityLevelConflictError(
                        "يوجد بالفعل سجل توافق General لنفس القطعة/الفئة؛ "
                        "لا يجوز إضافة سجل Year-specific لنفس الزوج (§13)."
                    )

                # §14: فحص تكرار الهدف الدقيق (الفهارس الجزئية تبقى الحارس الأخير على مستوى DB)
                cur.execute(
                    "SELECT COUNT(*) AS c FROM cmp.compatibility_records WHERE catalog_part_ref_id = %(part_id)s "
                    "AND trim_ref_id IS NOT DISTINCT FROM %(trim_id)s "
                    "AND trim_model_year_ref_id IS NOT DISTINCT FROM %(tmy_id)s",
                    {"part_id": catalog_part_ref_id, "trim_id": trim_ref_id, "tmy_id": trim_model_year_ref_id},
                )
                if cur.fetchone()["c"] > 0:
                    from cmp_service import DuplicateCompatibilityRecordError
                    raise DuplicateCompatibilityRecordError("سجل توافق بهذا الهدف بالضبط موجود بالفعل.")

                cur.execute(
                    "INSERT INTO cmp.compatibility_records "
                    "(catalog_part_ref_id, trim_ref_id, trim_model_year_ref_id, status) "
                    "VALUES (%(part_id)s, %(trim_id)s, %(tmy_id)s, 'active') "
                    "RETURNING id",
                    {"part_id": catalog_part_ref_id, "trim_id": trim_ref_id, "tmy_id": trim_model_year_ref_id},
                )
                new_id = cur.fetchone()["id"]

        return CompatibilityRecord(
            id=new_id, catalog_part_ref_id=catalog_part_ref_id,
            trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id,
            fitment_type=fitment_type, compatibility_notes=compatibility_notes, source=source,
        )

    def has_general_compatibility(self, catalog_part_ref_id: str, trim_ref_id: str) -> bool:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM cmp.compatibility_records "
                "WHERE catalog_part_ref_id = %(part_id)s AND trim_ref_id = %(trim_id)s AND status = 'active') AS ex",
                {"part_id": catalog_part_ref_id, "trim_id": trim_ref_id},
            )
            return cur.fetchone()["ex"]

    def has_any_year_specific_compatibility(self, catalog_part_ref_id: str, trim_ref_id: str) -> bool:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM cmp.compatibility_records cr "
                "JOIN vct.trim_model_years tmy ON tmy.id = cr.trim_model_year_ref_id "
                "WHERE cr.catalog_part_ref_id = %(part_id)s AND tmy.trim_ref_id = %(trim_id)s AND cr.status = 'active') AS ex",
                {"part_id": catalog_part_ref_id, "trim_id": trim_ref_id},
            )
            return cur.fetchone()["ex"]

    def has_year_specific_compatibility_for_year(self, catalog_part_ref_id: str, trim_ref_id: str, year: int) -> bool:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM cmp.compatibility_records cr "
                "JOIN vct.trim_model_years tmy ON tmy.id = cr.trim_model_year_ref_id "
                "WHERE cr.catalog_part_ref_id = %(part_id)s AND tmy.trim_ref_id = %(trim_id)s "
                "AND tmy.year = %(year)s AND cr.status = 'active') AS ex",
                {"part_id": catalog_part_ref_id, "trim_id": trim_ref_id, "year": year},
            )
            return cur.fetchone()["ex"]


class InMemoryCmpRepository(CmpRepository):
    """
    تنفيذ وهمي للاختبار فقط. لا قفل حقيقي (أحادي الخيط)؛ نفس قاعدة منع
    التعايش مُطبَّقة مباشرة. trim_model_year_resolver دالة محقونة اختيارية
    (عادة InMemoryVctRepository.get_trim_ref_id_for_trim_model_year) —
    لا استيراد مباشر لـVctRepository هنا؛ الاختبار هو من يربط الاثنين.
    """

    def __init__(self, trim_model_year_resolver=None):
        self._records = {}
        self._next_seq = 1
        self._trim_model_year_resolver = trim_model_year_resolver or (lambda _id: None)

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

    def get_trim_ref_id_for_trim_model_year(self, trim_model_year_id: str) -> Optional[str]:
        return self._trim_model_year_resolver(trim_model_year_id)

    def insert_compatibility_record_with_lock(
        self, catalog_part_ref_id: str, resolved_trim_ref_id: str,
        trim_ref_id: Optional[str], trim_model_year_ref_id: Optional[str],
        fitment_type: str, compatibility_notes: Optional[str], source: str,
    ) -> CompatibilityRecord:
        general_records = [
            r for r in self._records.values()
            if r.catalog_part_ref_id == catalog_part_ref_id and r.trim_ref_id == resolved_trim_ref_id
        ]
        year_specific_records = [
            r for r in self._records.values()
            if r.catalog_part_ref_id == catalog_part_ref_id and r.trim_model_year_ref_id is not None
            and self._trim_model_year_resolver(r.trim_model_year_ref_id) == resolved_trim_ref_id
        ]

        if trim_ref_id is not None and year_specific_records:
            raise CompatibilityLevelConflictError(
                "توجد بالفعل سجلات توافق Year-specific لنفس القطعة/الفئة؛ "
                "لا يجوز إضافة سجل General لنفس الزوج (§13)."
            )
        if trim_model_year_ref_id is not None and general_records:
            raise CompatibilityLevelConflictError(
                "يوجد بالفعل سجل توافق General لنفس القطعة/الفئة؛ "
                "لا يجوز إضافة سجل Year-specific لنفس الزوج (§13)."
            )
        for existing in self._records.values():
            if existing.catalog_part_ref_id == catalog_part_ref_id \
                    and existing.trim_ref_id == trim_ref_id \
                    and existing.trim_model_year_ref_id == trim_model_year_ref_id:
                from cmp_service import DuplicateCompatibilityRecordError
                raise DuplicateCompatibilityRecordError("سجل توافق بهذا الهدف بالضبط موجود بالفعل.")

        record = CompatibilityRecord(
            id=f"cmp-{self._next_seq}", catalog_part_ref_id=catalog_part_ref_id,
            trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id,
            fitment_type=fitment_type, compatibility_notes=compatibility_notes, source=source,
        )
        self._next_seq += 1
        self._records[record.id] = record
        return record

    def has_general_compatibility(self, catalog_part_ref_id: str, trim_ref_id: str) -> bool:
        return any(r.catalog_part_ref_id == catalog_part_ref_id and r.trim_ref_id == trim_ref_id
                   and r.status == "active" for r in self._records.values())

    def has_any_year_specific_compatibility(self, catalog_part_ref_id: str, trim_ref_id: str) -> bool:
        return any(
            r.catalog_part_ref_id == catalog_part_ref_id and r.trim_model_year_ref_id is not None
            and r.status == "active" and self._trim_model_year_resolver(r.trim_model_year_ref_id) == trim_ref_id
            for r in self._records.values()
        )

    def has_year_specific_compatibility_for_year(self, catalog_part_ref_id: str, trim_ref_id: str, year: int) -> bool:
        # InMemory يحتاج معرفة السنة الفعلية أيضًا؛ يُستهلَك عبر _year_lookup اختياري
        resolver_year = getattr(self, "_year_lookup", None)
        if resolver_year is None:
            return False
        for r in self._records.values():
            if r.catalog_part_ref_id == catalog_part_ref_id and r.trim_model_year_ref_id is not None \
                    and r.status == "active" and self._trim_model_year_resolver(r.trim_model_year_ref_id) == trim_ref_id:
                if resolver_year(r.trim_model_year_ref_id) == year:
                    return True
        return False

    def set_year_lookup(self, year_lookup):
        """اختباري فقط: يربط trim_model_year_id → year الفعلية (عادة من InMemoryVctRepository)."""
        self._year_lookup = year_lookup
