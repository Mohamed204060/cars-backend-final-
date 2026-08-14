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
    trim_ref_id: Optional[str] = None          # NULL يعني سجل Year-specific
    trim_model_year_ref_id: Optional[str] = None  # NULL يعني سجل General
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


class InvalidCompatibilityTargetError(Exception):
    """Approved VCT Design Baseline §10: يجب تحديد هدف واحد بالضبط — إما trim_ref_id أو trim_model_year_ref_id."""


class CompatibilityLevelConflictError(Exception):
    """§13، §16: لا تعايش General وYear-specific لنفس زوج (القطعة، الفئة الأصلية)."""


class TrimModelYearNotFoundError(Exception):
    """Batch 1: سنة موديل غير موجودة عند إنشاء سجل توافق Year-specific."""


ALLOWED_TRANSITIONS = {
    "active": {"archived"},
    "archived": set(),
}


def validate_compatibility_target(trim_ref_id: Optional[str], trim_model_year_ref_id: Optional[str]) -> None:
    """§10: Exactly One Compatibility Target."""
    has_trim = trim_ref_id is not None
    has_year = trim_model_year_ref_id is not None
    if has_trim == has_year:
        raise InvalidCompatibilityTargetError(
            "يجب تحديد هدف واحد بالضبط: إما trim_ref_id (General) أو trim_model_year_ref_id (Year-specific)، لا كلاهما ولا لا شيء."
        )


def create_compatibility_record(
    catalog_part_ref_id: str,
    resolved_trim_ref_id: str,
    existing_records_for_exact_target: list,
    is_part_approved_checker: Callable[[str], bool],
    is_trim_valid_checker: Callable[[str], bool],
    trim_ref_id: Optional[str] = None,
    trim_model_year_ref_id: Optional[str] = None,
    fitment_type: str = "unknown",
    compatibility_notes: Optional[str] = None,
    source: str = "catalog_admin",
) -> CompatibilityRecord:
    """
    مبدأ SSOT مطبَّق حرفيًا: هذه الدالة لا تستعلم عن بيانات PCT أو VCT مباشرة؛
    تعتمد حصرًا على الدالتين المحقونتين (Dependency Injection) القادمتين من
    كل خدمة على حدة. resolved_trim_ref_id هو الفئة الأصلية (Underlying Trim)
    سواء كان الهدف General (=trim_ref_id) أو Year-specific (محلولاً من
    trim_model_year_ref_id مسبقًا في via_repository) — يُستخدَم فقط للتحقق
    من صحة الفئة (is_trim_valid_checker)، لا يُخزَّن بذاته إن كان الهدف Year-specific.

    ملاحظة نطاق: existing_records_for_exact_target يغطي فقط فحص التكرار
    (REQ-CMP-002/§14 Partial Unique)؛ فحص التعايش العام/السنوي (§13) مسؤولية
    طبقة Repository عبر Advisory Lock، لا هذه الدالة الخالصة.
    """
    validate_compatibility_target(trim_ref_id, trim_model_year_ref_id)

    if not is_part_approved_checker(catalog_part_ref_id):
        raise PartNotApprovedForCompatibilityError(
            f"قطعة الكتالوج '{catalog_part_ref_id}' غير معتمدة؛ لا يمكن إنشاء سجل توافق لها."
        )
    if not is_trim_valid_checker(resolved_trim_ref_id):
        raise TrimNotValidForCompatibilityError(
            f"فئة السيارة '{resolved_trim_ref_id}' غير صالحة؛ لا يمكن إنشاء سجل توافق لها."
        )

    # REQ-CMP-002/§14: منع التكرار لنفس الهدف الدقيق (General أو Year-specific بعينها)
    for existing in existing_records_for_exact_target:
        if existing.catalog_part_ref_id == catalog_part_ref_id \
                and existing.trim_ref_id == trim_ref_id \
                and existing.trim_model_year_ref_id == trim_model_year_ref_id:
            raise DuplicateCompatibilityRecordError(
                "سجل توافق بهذا الهدف بالضبط (القطعة، الفئة/السنة) موجود بالفعل."
            )

    if fitment_type not in VALID_FITMENT_TYPES:
        raise ValueError(f"نوع توافق غير معروف: {fitment_type}")
    if source not in VALID_SOURCES:
        raise ValueError(f"مصدر بيانات توافق غير معروف: {source}")

    return CompatibilityRecord(
        id="", catalog_part_ref_id=catalog_part_ref_id,
        trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id,
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
    repository, catalog_part_ref_id: str,
    is_part_approved_checker, is_trim_valid_checker,
    trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
    fitment_type: str = "unknown", compatibility_notes: Optional[str] = None, source: str = "catalog_admin",
) -> CompatibilityRecord:
    """
    §16: يحل trim_model_year_ref_id إلى resolved_trim_ref_id (إن وُجد) قبل
    أي تحقق، ثم يُفوِّض القفل + إعادة فحص التعايش (§13) + فحص التكرار + الإدراج
    بالكامل لطبقة Repository — نفس مبدأ الفصل المتَّبع في
    add_market_availability_via_repository (VCT، القسم نفسه من هذه الدفعة).
    """
    validate_compatibility_target(trim_ref_id, trim_model_year_ref_id)

    if trim_ref_id is not None:
        resolved_trim_ref_id = trim_ref_id
    else:
        resolved_trim_ref_id = repository.get_trim_ref_id_for_trim_model_year(trim_model_year_ref_id)
        if resolved_trim_ref_id is None:
            raise TrimModelYearNotFoundError(f"لا توجد سنة موديل بالمعرّف: {trim_model_year_ref_id}")

    if not is_part_approved_checker(catalog_part_ref_id):
        raise PartNotApprovedForCompatibilityError(
            f"قطعة الكتالوج '{catalog_part_ref_id}' غير معتمدة؛ لا يمكن إنشاء سجل توافق لها."
        )
    if not is_trim_valid_checker(resolved_trim_ref_id):
        raise TrimNotValidForCompatibilityError(
            f"فئة السيارة '{resolved_trim_ref_id}' غير صالحة؛ لا يمكن إنشاء سجل توافق لها."
        )
    if fitment_type not in VALID_FITMENT_TYPES:
        raise ValueError(f"نوع توافق غير معروف: {fitment_type}")
    if source not in VALID_SOURCES:
        raise ValueError(f"مصدر بيانات توافق غير معروف: {source}")

    return repository.insert_compatibility_record_with_lock(
        catalog_part_ref_id=catalog_part_ref_id, resolved_trim_ref_id=resolved_trim_ref_id,
        trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id,
        fitment_type=fitment_type, compatibility_notes=compatibility_notes, source=source,
    )


# ---------------------------------------------------------------------------
# Approved VCT Design Baseline §18: Search Semantics مع Compatibility —
# دلالة القراءة الموحَّدة، تُستهلَك من كل Read Path (Search، وأي مكان آخر
# يحتاج التحقق من توافق قطعة/فئة).
# ---------------------------------------------------------------------------

def is_part_compatible_with_trim(
    has_general_record: bool, has_any_year_specific_record: bool,
    has_year_specific_record_for_requested_year: bool, requested_year: Optional[int] = None,
) -> bool:
    """
    §18: بلا سنة محدَّدة (requested_year=None) → مرشَّحة إذا وُجد General أو
    أي Year-specific تحت نفس Trim (بغضّ النظر عن أي سنة بعينها). بسنة
    محدَّدة Y → فقط General أو Year-specific لنفس (Trim, Y) بالضبط؛ لا
    تُعتبَر Year-specific لسنة أخرى مطابقة إطلاقًا.

    مُصمَّمة لتطابق تمامًا نتيجة EXISTS المكافئة في SQL (search_repository)؛
    الثلاثة معاملات المنطقية تُحسَب هناك مباشرة عبر استعلام واحد، لا عبر
    جلب كل السجلات ثم الترشيح في بايثون (لا N+1، بنفس مبدأ CR-021).
    """
    if requested_year is None:
        return has_general_record or has_any_year_specific_record
    return has_general_record or has_year_specific_record_for_requested_year


def archive_compatibility_record_via_repository(repository, record_id: str) -> CompatibilityRecord:
    """امتداد CMP Contract Extension: REQ-CMP-003، لم يكن الغلاف موجودًا رغم
    وجود transition_compatibility_status النقية أصلًا."""
    record = repository.get_record_by_id(record_id)
    if record is None:
        raise CompatibilityRecordNotFoundError(f"لا يوجد سجل توافق بالمعرّف: {record_id}")
    transition_compatibility_status(record, "archived")
    return repository.update_record(record)
