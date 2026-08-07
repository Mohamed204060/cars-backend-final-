"""
ref_service.py — منطق خدمة البيانات المرجعية (REF)
المرجع: REQ-REF-001..009 (يشمل إطار الاستيراد الجماعي، CR-003)

ملاحظة نطاق: هذا أول كود لهذه الخدمة إطلاقًا (لم يكن هناك أي Service أو
Repository لها من قبل، خلافًا لكل الخدمات الأخرى التي احتوت منطقًا جاهزًا
سبق بناء REST له فقط). صيغة ملف الاستيراد المفترَضة (عمود واحد باسم "code"،
صف عناوين ثم صفوف بيانات) قرار تنفيذي عملي بسيط اتُّخذ لعدم وجود مواصفة
أدق للأعمدة في REQ-REF-005/009؛ قابل للتعديل لاحقًا دون أثر على المخطط.
"""

from dataclasses import dataclass, field
from typing import List, Optional


REF_TYPES = {
    "country", "city", "language", "fuel_type", "transmission_type",
    "engine_type", "part_condition", "subscription_type",
}
REF_VALUE_STATUSES = {"active", "archived"}
BULK_IMPORT_JOB_STATUSES = {"validating", "preview_ready", "committed", "failed"}
ROW_OUTCOMES = {"new", "updated", "rejected"}


@dataclass
class RefValue:
    id: str
    ref_type: str
    code: str
    status: str = "active"


@dataclass
class BulkImportRowResult:
    row_number: int
    outcome: str
    raw_row_data: dict
    rejection_reason: Optional[str] = None


@dataclass
class BulkImportJob:
    id: str
    ref_type: str
    imported_by_ref_id: str
    file_name: str
    status: str = "validating"
    new_count: int = 0
    updated_count: int = 0
    rejected_count: int = 0
    rows: List[BulkImportRowResult] = field(default_factory=list)


class InvalidRefTypeError(Exception):
    """REQ-REF-001: نوع بيانات مرجعية غير معروف من الأنواع الثمانية المعتمَدة."""


class RefValueArchivedImmutableError(Exception):
    """REQ-REF-002: لا حذف نهائي؛ محاولة أرشفة قيمة مؤرشَفة أصلًا لا أثر إضافيًا لها."""


class DuplicateRefValueError(Exception):
    """(ref_type, code) يجب أن يكون فريدًا (uq_ref_values_type_code)."""


def create_ref_value(ref_type: str, code: str, existing_codes_for_type: List[str]) -> RefValue:
    if ref_type not in REF_TYPES:
        raise InvalidRefTypeError(f"نوع بيانات مرجعية غير معروف: {ref_type}")
    if not code or not code.strip():
        raise ValueError("رمز القيمة المرجعية (code) يجب ألا يكون فارغًا.")
    if code in existing_codes_for_type:
        raise DuplicateRefValueError(f"القيمة '{code}' موجودة بالفعل ضمن النوع '{ref_type}'.")
    return RefValue(id="", ref_type=ref_type, code=code)


def archive_ref_value(value: RefValue) -> RefValue:
    if value.status == "archived":
        raise RefValueArchivedImmutableError("هذه القيمة مؤرشَفة بالفعل.")
    value.status = "archived"
    return value


def classify_import_rows(ref_type: str, rows: List[dict], existing_codes_for_type: List[str]) -> List[BulkImportRowResult]:
    if ref_type not in REF_TYPES:
        raise InvalidRefTypeError(f"نوع بيانات مرجعية غير معروف: {ref_type}")

    results: List[BulkImportRowResult] = []
    seen_in_file = set()
    for i, row in enumerate(rows, start=1):
        code = (row.get("code") or "").strip()
        if not code:
            results.append(BulkImportRowResult(row_number=i, outcome="rejected", raw_row_data=row,
                                                 rejection_reason="عمود code فارغ أو غير موجود."))
            continue
        if code in seen_in_file:
            results.append(BulkImportRowResult(row_number=i, outcome="rejected", raw_row_data=row,
                                                 rejection_reason=f"تكرار للرمز '{code}' داخل نفس الملف."))
            continue
        seen_in_file.add(code)
        outcome = "updated" if code in existing_codes_for_type else "new"
        results.append(BulkImportRowResult(row_number=i, outcome=outcome, raw_row_data=row))
    return results


def create_ref_value_via_repository(repository, ref_type: str, code: str) -> RefValue:
    existing = repository.get_values_for_type(ref_type, include_archived=True)
    existing_codes = [v.code for v in existing]
    value = create_ref_value(ref_type, code, existing_codes)
    return repository.insert_value(value)


def archive_ref_value_via_repository(repository, value_id: str) -> RefValue:
    value = repository.get_value_by_id(value_id)
    if value is None:
        raise ValueError(f"لا توجد قيمة مرجعية بالمعرّف: {value_id}")
    archive_ref_value(value)
    return repository.update_value(value)


def preview_bulk_import_via_repository(
    repository, ref_type: str, file_name: str, imported_by_ref_id: str, parsed_rows: List[dict],
) -> BulkImportJob:
    existing = repository.get_values_for_type(ref_type, include_archived=True)
    existing_codes = [v.code for v in existing]
    row_results = classify_import_rows(ref_type, parsed_rows, existing_codes)

    job = BulkImportJob(
        id="", ref_type=ref_type, imported_by_ref_id=imported_by_ref_id, file_name=file_name,
        status="preview_ready",
        new_count=sum(1 for r in row_results if r.outcome == "new"),
        updated_count=sum(1 for r in row_results if r.outcome == "updated"),
        rejected_count=sum(1 for r in row_results if r.outcome == "rejected"),
        rows=row_results,
    )
    return repository.insert_bulk_import_job(job)
