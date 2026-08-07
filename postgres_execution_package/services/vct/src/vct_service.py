"""
vct_service.py — منطق خدمة كتالوج السيارات (VCT)
المرجع: REQ-VCT-001..007

هذه الخدمة المرجع الرسمي الوحيد (SSOT) لبيانات السيارات (الشركة المصنّعة،
الموديل، الجيل، الفئة)؛ لا تُخزَّن أي بيانات توافق مع قطع الغيار هنا —
ذلك مجال منفصل تمامًا (CMP)، تمامًا كما تبقى بيانات القطع في PCT وحدها.
"""

from dataclasses import dataclass
from typing import Optional


VALID_STATUSES = {"proposed", "approved", "archived"}

ALLOWED_TRANSITIONS = {
    "proposed": {"approved", "archived"},
    "approved": {"archived"},
    "archived": set(),
}


@dataclass
class Manufacturer:
    id: str
    status: str = "proposed"


@dataclass
class Model:
    id: str
    manufacturer_id: str  # داخل نفس الخدمة (VCT)؛ ليس معرّفًا عابرًا لخدمة أخرى
    status: str = "proposed"


@dataclass
class Generation:
    id: str
    model_id: str


@dataclass
class Trim:
    id: str
    generation_id: str
    fuel_type_ref_id: str        # SSOT: إشارة مرجعية لخدمة REF فقط
    transmission_type_ref_id: str  # SSOT: إشارة مرجعية لخدمة REF فقط


class InvalidVctStatusError(Exception):
    """REQ-VCT-002: انتقال حالة غير مسموح به لشركة مصنّعة أو موديل."""


class ManufacturerNotApprovedError(Exception):
    """REQ-VCT-003: لا يجوز إنشاء موديل تحت شركة مصنّعة غير معتمَدة."""


class NotFoundError(Exception):
    """امتداد VCT Contract Extension: كيان أب غير موجود (شركة مصنّعة/موديل/جيل)."""


# ---------------------------------------------------------------------------
# REQ-VCT-001, 002: دورة حياة الشركة المصنّعة والموديل (نفس نمط PCT)
# ---------------------------------------------------------------------------

def propose_manufacturer() -> Manufacturer:
    return Manufacturer(id="", status="proposed")


def transition_manufacturer_status(manufacturer: Manufacturer, new_status: str) -> Manufacturer:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"حالة غير معروفة: {new_status}")
    allowed = ALLOWED_TRANSITIONS.get(manufacturer.status, set())
    if new_status not in allowed:
        raise InvalidVctStatusError(
            f"الانتقال من '{manufacturer.status}' إلى '{new_status}' غير مسموح به."
        )
    manufacturer.status = new_status
    return manufacturer


def is_manufacturer_approved(manufacturer: Manufacturer) -> bool:
    return manufacturer.status == "approved"


def propose_model(manufacturer_id: str) -> Model:
    return Model(id="", manufacturer_id=manufacturer_id, status="proposed")


def transition_model_status(model: Model, new_status: str) -> Model:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"حالة غير معروفة: {new_status}")
    allowed = ALLOWED_TRANSITIONS.get(model.status, set())
    if new_status not in allowed:
        raise InvalidVctStatusError(
            f"الانتقال من '{model.status}' إلى '{new_status}' غير مسموح به."
        )
    model.status = new_status
    return model


def is_model_approved(model: Model) -> bool:
    return model.status == "approved"


# ---------------------------------------------------------------------------
# REQ-VCT-003, 004: الجيل والفئة (تجميعات تابعة، بلا دورة حياة مستقلة)
# ---------------------------------------------------------------------------

def create_generation(model_id: str) -> Generation:
    return Generation(id="", model_id=model_id)


def create_trim(generation_id: str, fuel_type_ref_id: str, transmission_type_ref_id: str) -> Trim:
    if not fuel_type_ref_id or not transmission_type_ref_id:
        raise ValueError("نوع الوقود وناقل الحركة إلزاميان لإنشاء فئة سيارة.")
    return Trim(id="", generation_id=generation_id,
                fuel_type_ref_id=fuel_type_ref_id, transmission_type_ref_id=transmission_type_ref_id)


# ---------------------------------------------------------------------------
# نقطة التحقق المرجعية الرسمية لوجود فئة سيارة معتمدة (تُستهلَك من خدمة CMP)
# ---------------------------------------------------------------------------

def is_trim_valid_for_compatibility(trim: Optional[Trim]) -> bool:
    """
    SSOT: تتحقق فقط من وجود الفئة كسجل صالح ضمن VCT (لا دورة حياة اعتماد
    منفصلة للفئة نفسها في هذا الإصدار)؛ خدمة CMP تستهلك هذه الدالة عبر حقن
    الاعتمادية، لا استعلامًا مباشرًا لبيانات VCT.
    """
    return trim is not None


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4)
# ---------------------------------------------------------------------------

def propose_manufacturer_via_repository(repository) -> Manufacturer:
    return repository.insert_manufacturer(propose_manufacturer())


def approve_manufacturer_via_repository(repository, manufacturer_id: str) -> Manufacturer:
    manufacturer = repository.get_manufacturer_by_id(manufacturer_id)
    if manufacturer is None:
        raise ValueError(f"لا توجد شركة مصنّعة بالمعرّف: {manufacturer_id}")
    transition_manufacturer_status(manufacturer, "approved")
    return repository.update_manufacturer(manufacturer)


def create_full_trim_via_repository(repository, manufacturer_id: str,
                                     fuel_type_ref_id: str, transmission_type_ref_id: str) -> Trim:
    """يُنشئ موديلاً وجيلاً وفئة مرتبطين تحت شركة مصنّعة قائمة، في تسلسل واحد مبسَّط للاختبار."""
    model = repository.insert_model(propose_model(manufacturer_id))
    generation = repository.insert_generation(create_generation(model.id))
    trim = create_trim(generation.id, fuel_type_ref_id, transmission_type_ref_id)
    return repository.insert_trim(trim)


# ---------------------------------------------------------------------------
# امتداد VCT Contract Extension: أغلفة *_via_repository الحبيبية الناقصة،
# بنفس نمط الأغلفة أعلاه، مع تطبيق REQ-VCT-003 صراحةً (لم يكن مُتحقَّقًا).
# ---------------------------------------------------------------------------

def propose_model_via_repository(repository, manufacturer_id: str) -> Model:
    manufacturer = repository.get_manufacturer_by_id(manufacturer_id)
    if manufacturer is None:
        raise NotFoundError(f"لا توجد شركة مصنّعة بالمعرّف: {manufacturer_id}")
    if not is_manufacturer_approved(manufacturer):
        raise ManufacturerNotApprovedError(
            "لا يمكن إضافة موديل تحت شركة مصنّعة غير معتمَدة (REQ-VCT-003)."
        )
    return repository.insert_model(propose_model(manufacturer_id))


def create_generation_via_repository(repository, model_id: str) -> Generation:
    model = repository.get_model_by_id(model_id)
    if model is None:
        raise NotFoundError(f"لا يوجد موديل بالمعرّف: {model_id}")
    return repository.insert_generation(create_generation(model_id))


def create_trim_via_repository(
    repository, generation_id: str, fuel_type_ref_id: str, transmission_type_ref_id: str
) -> Trim:
    generation = repository.get_generation_by_id(generation_id)
    if generation is None:
        raise NotFoundError(f"لا يوجد جيل بالمعرّف: {generation_id}")
    trim = create_trim(generation_id, fuel_type_ref_id, transmission_type_ref_id)
    return repository.insert_trim(trim)
