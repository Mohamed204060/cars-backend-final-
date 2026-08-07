"""
cmp_service.py — منطق خدمة التوافق (CMP)
المرجع: REQ-CMP-001..003

هذا هو مفهوم "Part Fitment" الذي طلب المالك اعتماده: علاقة مستقلة تمامًا
تربط مرجع قطعة كتالوج (من PCT) بمرجع فئة سيارة (من VCT)، دون تخزين أي
تفاصيل توافق داخل PCT نفسها؛ يدعم هذا علاقة N:M طبيعية بين القطع والسيارات
دون أي تكرار بيانات، ودون زيادة الترابط المباشر بين خدمتَي PCT وVCT
أنفسهما — كلتاهما تُستهلَكان هنا فقط عبر دالتَي تحقق محقونتين.
"""

from dataclasses import dataclass
from typing import Callable, Optional


VALID_STATUSES = {"active", "archived"}


VALID_FITMENT_TYPES = {"exact_fit", "compatible", "requires_modification", "not_compatible", "unknown"}
VALID_SOURCES = {"manufacturer", "catalog_admin", "import", "merchant_proposal", "user_proposal"}


@dataclass
class CompatibilityRecord:
    id: str
    catalog_part_ref_id: str  # SSOT: إشارة مرجعية فقط لخدمة PCT
    trim_ref_id: str          # SSOT: إشارة مرجعية فقط لخدمة VCT
    status: str = "active"
    fitment_type: str = "unknown"          # مقترح المالك: دقة التوافق
    compatibility_notes: Optional[str] = None  # مقترح المالك: ملاحظات حرة
    source: str = "catalog_admin"          # مقترح المالك: مصدر بيانات التوافق
    # ملاحظة Backlog: approval_status (draft/approved/archived) منفصل عن status،
    # وapproved_by/approved_at، ودعم Versioning — تُرجَأ عمدًا؛ تتقاطع مع دورة
    # حياة status الحالية (active/archived) وتستوجب قرار تصميم مستقل لتحديد
    # العلاقة بينهما، لا إضافة حقول متراكبة دون توضيح دلالتها أولاً (YAGNI).


class PartNotApprovedForCompatibilityError(Exception):
    """REQ-CMP-001: لا يجوز إنشاء سجل توافق إلا لقطعة كتالوج معتمدة."""


class TrimNotValidForCompatibilityError(Exception):
    """REQ-CMP-001: لا يجوز إنشاء سجل توافق إلا لفئة سيارة صالحة."""


class DuplicateCompatibilityRecordError(Exception):
    """REQ-CMP-002: منع تكرار سجل التوافق لنفس زوج (القطعة، الفئة)."""


class InvalidCompatibilityStatusError(Exception):
    """REQ-CMP-003: انتقال حالة غير مسموح به."""


class CompatibilityRecordNotFoundError(Exception):
    """امتداد CMP Contract Extension: سجل توافق غير موجود."""


ALLOWED_TRANSITIONS = {
    "active": {"archived"},
    "archived": set(),
}


def create_compatibility_record(
    catalog_part_ref_id: str,
    trim_ref_id: str,
    existing_records_for_pair: list,
    is_part_approved_checker: Callable[[str], bool],
    is_trim_valid_checker: Callable[[str], bool],
    fitment_type: str = "unknown",
    compatibility_notes: Optional[str] = None,
    source: str = "catalog_admin",
) -> CompatibilityRecord:
    """
    مبدأ SSOT مطبَّق حرفيًا: هذه الدالة لا تستعلم عن بيانات PCT أو VCT مباشرة؛
    تعتمد حصرًا على الدالتين المحقونتين (Dependency Injection) القادمتين من
    كل خدمة على حدة.
    """
    if not is_part_approved_checker(catalog_part_ref_id):
        raise PartNotApprovedForCompatibilityError(
            f"قطعة الكتالوج '{catalog_part_ref_id}' غير معتمدة؛ لا يمكن إنشاء سجل توافق لها."
        )
    if not is_trim_valid_checker(trim_ref_id):
        raise TrimNotValidForCompatibilityError(
            f"فئة السيارة '{trim_ref_id}' غير صالحة؛ لا يمكن إنشاء سجل توافق لها."
        )

    # REQ-CMP-002: منع التكرار لنفس الزوج (القطعة، الفئة)
    for existing in existing_records_for_pair:
        if existing.catalog_part_ref_id == catalog_part_ref_id and existing.trim_ref_id == trim_ref_id:
            raise DuplicateCompatibilityRecordError(
                "سجل توافق بهذا الزوج (القطعة، الفئة) موجود بالفعل."
            )

    if fitment_type not in VALID_FITMENT_TYPES:
        raise ValueError(f"نوع توافق غير معروف: {fitment_type}")
    if source not in VALID_SOURCES:
        raise ValueError(f"مصدر بيانات توافق غير معروف: {source}")

    return CompatibilityRecord(
        id="", catalog_part_ref_id=catalog_part_ref_id, trim_ref_id=trim_ref_id,
        fitment_type=fitment_type, compatibility_notes=compatibility_notes, source=source,
    )


def build_administrative_audit_event(action: str, actor_ref_id: str, record_id: str, reason: Optional[str] = None):
    """
    مقترح المالك: نقطة جاهزة لتسجيل تغييرات بيانات التوافق في سجل النشاط
    الإداري عند اكتمال الربط الفعلي مع خدمة AUD لاحقًا؛ لا كتابة فعلية هنا.
    """
    allowed_actions = {"compatibility_created", "compatibility_archived", "compatibility_reactivated"}
    if action not in allowed_actions:
        raise ValueError(f"نوع حدث غير معروف: {action}")
    return {"log_type": "administrative", "event_name": action, "actor_ref_id": actor_ref_id,
            "metadata": {"record_id": record_id, "reason": reason}}


def transition_compatibility_status(record: CompatibilityRecord, new_status: str) -> CompatibilityRecord:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"حالة غير معروفة: {new_status}")
    allowed = ALLOWED_TRANSITIONS.get(record.status, set())
    if new_status not in allowed:
        raise InvalidCompatibilityStatusError(
            f"الانتقال من '{record.status}' إلى '{new_status}' غير مسموح به."
        )
    record.status = new_status
    return record


# ---------------------------------------------------------------------------
# نقطة تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4)
# ---------------------------------------------------------------------------

def create_compatibility_record_via_repository(
    repository, catalog_part_ref_id: str, trim_ref_id: str,
    is_part_approved_checker, is_trim_valid_checker,
) -> CompatibilityRecord:
    existing_for_part = repository.get_records_for_part(catalog_part_ref_id)
    record = create_compatibility_record(
        catalog_part_ref_id, trim_ref_id, existing_for_part,
        is_part_approved_checker, is_trim_valid_checker,
    )
    return repository.insert_record(record)


def archive_compatibility_record_via_repository(repository, record_id: str) -> CompatibilityRecord:
    """امتداد CMP Contract Extension: REQ-CMP-003، لم يكن الغلاف موجودًا رغم
    وجود transition_compatibility_status النقية أصلًا."""
    record = repository.get_record_by_id(record_id)
    if record is None:
        raise CompatibilityRecordNotFoundError(f"لا يوجد سجل توافق بالمعرّف: {record_id}")
    transition_compatibility_status(record, "archived")
    return repository.update_record(record)
