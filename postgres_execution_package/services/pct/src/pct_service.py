"""
pct_service.py — منطق خدمة كتالوج قطع الغيار (PCT)
المرجع: REQ-PCT-001..007

هذه الخدمة المرجع الرسمي الوحيد (SSOT) لقطعة الكتالوج وأسمائها وأرقامها؛
أي خدمة أخرى (كعنصر المخزون STR) تشير إليها بمعرّف مرجعي فقط ولا تكرّر
بياناتها، ولا تعتمد على أسماء نصية لتحديد القطعة.
"""

from dataclasses import dataclass, field
from typing import Optional, List


VALID_STATUSES = {"proposed", "approved", "archived"}
VALID_NAME_KINDS = {"canonical", "local", "english", "synonym"}


@dataclass
class CatalogPart:
    id: str
    category_id: str
    status: str = "proposed"  # REQ-PCT-002


@dataclass
class LocalizedName:
    id: str
    catalog_part_id: str
    name_value: str
    name_kind: str
    locale: Optional[str] = None


@dataclass
class OemNumber:
    id: str
    catalog_part_id: str
    manufacturer_ref_id: str
    oem_number: str


class InvalidCatalogPartStatusError(Exception):
    """REQ-PCT-002: انتقال حالة غير مسموح به لقطعة الكتالوج."""


class DuplicateOemNumberError(Exception):
    """REQ-PCT-005: منع تكرار رقم OEM لنفس الشركة المصنّعة."""


# REQ-PCT-002: انتقالات الحالة المسموحة لقطعة الكتالوج (نفس نمط STR)
ALLOWED_PART_TRANSITIONS = {
    "proposed": {"approved", "archived"},
    "approved": {"archived"},
    "archived": set(),
}


# ---------------------------------------------------------------------------
# REQ-PCT-001, 002: اقتراح قطعة كتالوج واعتمادها
# ---------------------------------------------------------------------------

def propose_catalog_part(category_id: str) -> CatalogPart:
    return CatalogPart(id="", category_id=category_id, status="proposed")


def transition_part_status(part: CatalogPart, new_status: str) -> CatalogPart:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"حالة غير معروفة: {new_status}")
    allowed = ALLOWED_PART_TRANSITIONS.get(part.status, set())
    if new_status not in allowed:
        raise InvalidCatalogPartStatusError(
            f"الانتقال من '{part.status}' إلى '{new_status}' غير مسموح به."
        )
    part.status = new_status
    return part


def is_approved(part: CatalogPart) -> bool:
    """نقطة الفحص المرجعية الوحيدة لتحديد ما إذا كانت القطعة معتمدة؛
    تستهلكها الخدمات الأخرى (كعنصر المخزون) عبر تكامل صريح، لا استعلامًا مباشرًا."""
    return part.status == "approved"


# ---------------------------------------------------------------------------
# REQ-PCT-003: الأسماء متعددة اللغات/الأنواع
# ---------------------------------------------------------------------------

def add_localized_name(part: CatalogPart, name_value: str, name_kind: str,
                        locale: Optional[str] = None) -> LocalizedName:
    if name_kind not in VALID_NAME_KINDS:
        raise ValueError(f"نوع اسم غير معروف: {name_kind}")
    if not name_value or not name_value.strip():
        raise ValueError("قيمة الاسم يجب ألا تكون فارغة.")
    return LocalizedName(id="", catalog_part_id=part.id, name_value=name_value,
                          name_kind=name_kind, locale=locale)


# ---------------------------------------------------------------------------
# REQ-PCT-004, 005: أرقام OEM بلا تكرار ضمن نفس الشركة المصنّعة
# ---------------------------------------------------------------------------

def add_oem_number(part: CatalogPart, manufacturer_ref_id: str, oem_number: str,
                    existing_oem_numbers: List[OemNumber]) -> OemNumber:
    for existing in existing_oem_numbers:
        if (existing.manufacturer_ref_id == manufacturer_ref_id
                and existing.oem_number.strip().lower() == oem_number.strip().lower()):
            raise DuplicateOemNumberError(
                f"رقم OEM '{oem_number}' مسجَّل بالفعل لهذه الشركة المصنّعة."
            )
    return OemNumber(id="", catalog_part_id=part.id, manufacturer_ref_id=manufacturer_ref_id,
                      oem_number=oem_number)


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4)
# ---------------------------------------------------------------------------

def propose_catalog_part_via_repository(repository, category_id: str) -> CatalogPart:
    part = propose_catalog_part(category_id)
    return repository.insert_part(part)


def approve_catalog_part_via_repository(repository, part_id: str) -> CatalogPart:
    part = repository.get_part_by_id(part_id)
    if part is None:
        raise ValueError(f"لا توجد قطعة كتالوج بالمعرّف: {part_id}")
    transition_part_status(part, "approved")
    return repository.update_part(part)


def add_oem_number_via_repository(repository, part_id: str, manufacturer_ref_id: str, oem_number: str) -> OemNumber:
    part = repository.get_part_by_id(part_id)
    if part is None:
        raise ValueError(f"لا توجد قطعة كتالوج بالمعرّف: {part_id}")
    existing = repository.get_oem_numbers_for_manufacturer(manufacturer_ref_id)
    new_oem = add_oem_number(part, manufacturer_ref_id, oem_number, existing)
    return repository.insert_oem_number(new_oem)


def add_localized_name_via_repository(
    repository, part_id: str, name_value: str, name_kind: str, locale: Optional[str] = None
) -> LocalizedName:
    """تعديل PCT Contract Extension: غلاف *_via_repository لم يكن موجودًا رغم
    وجود add_localized_name النقية أصلًا؛ نفس نمط approve/add_oem_number تمامًا."""
    part = repository.get_part_by_id(part_id)
    if part is None:
        raise ValueError(f"لا توجد قطعة كتالوج بالمعرّف: {part_id}")
    new_name = add_localized_name(part, name_value, name_kind, locale)
    return repository.insert_localized_name(new_name)


# ---------------------------------------------------------------------------
# CR-015: قائمة Frontend Enablement — بلا تعديل على أي دالة أعلاه
# ---------------------------------------------------------------------------

def list_parts_via_repository(repository, status: str, q: Optional[str], page: int, page_size: int):
    """صلاحية طلب status='proposed' تُحسَم في طبقة الـAPI (auth_repo + دور)،
    لا هنا — هذه الدالة تنفيذية فقط، بلا قرار تفويض."""
    return repository.list_parts(status, q, page, page_size)
