"""
vct_service.py — منطق خدمة كتالوج السيارات (VCT)
المرجع: REQ-VCT-001..007

هذه الخدمة المرجع الرسمي الوحيد (SSOT) لبيانات السيارات (الشركة المصنّعة،
الموديل، الجيل، الفئة)؛ لا تُخزَّن أي بيانات توافق مع قطع الغيار هنا —
ذلك مجال منفصل تمامًا (CMP)، تمامًا كما تبقى بيانات القطع في PCT وحدها.
"""

from dataclasses import dataclass
from typing import Optional, List


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
    start_year: Optional[int] = None  # Batch 1 §2: وصفي فقط، ليس مصدر الحقيقة للتوافق
    end_year: Optional[int] = None


@dataclass
class Trim:
    id: str
    generation_id: str
    fuel_type_ref_id: str        # SSOT: إشارة مرجعية لخدمة REF فقط
    transmission_type_ref_id: str  # SSOT: إشارة مرجعية لخدمة REF فقط


# ---------------------------------------------------------------------------
# Approved VCT Design Baseline §3-4: Trim Model Years (Batch 1)
# ---------------------------------------------------------------------------

@dataclass
class TrimModelYear:
    id: str
    trim_ref_id: str
    year: int


class YearOutOfGenerationRangeError(Exception):
    """§4: السنة المطلوبة خارج [generation.start_year, generation.end_year] الوصفيين."""


class TrimNotFoundError(Exception):
    """Batch 1: فئة سيارة غير موجودة عند إنشاء Trim Model Year."""


class DuplicateTrimModelYearError(Exception):
    """§3: UNIQUE(trim_ref_id, year) — نفس السنة لنفس الفئة موجودة بالفعل."""


def create_trim_model_year(
    trim_ref_id: str, year: int,
    generation_start_year: Optional[int], generation_end_year: Optional[int],
    existing_years_for_trim: Optional[list] = None,
) -> TrimModelYear:
    """
    §4: التحقق يتم بقراءة نطاق الجيل الأب (start_year/end_year) قبل
    الإدراج — يستدعيها via_repository داخل نفس Transaction (لا CHECK بنيوي
    لأنه يستوجب الوصول لصف آخر عبر trim_ref_id).
    """
    if generation_start_year is not None and year < generation_start_year:
        raise YearOutOfGenerationRangeError(
            f"السنة {year} أقل من بداية نطاق الجيل ({generation_start_year})."
        )
    if generation_end_year is not None and year > generation_end_year:
        raise YearOutOfGenerationRangeError(
            f"السنة {year} أكبر من نهاية نطاق الجيل ({generation_end_year})."
        )
    if existing_years_for_trim is not None and year in existing_years_for_trim:
        raise DuplicateTrimModelYearError(f"السنة {year} مسجَّلة بالفعل لهذه الفئة.")
    return TrimModelYear(id="", trim_ref_id=trim_ref_id, year=year)


def validate_generation_range_update_against_existing_years(
    new_start_year: Optional[int], new_end_year: Optional[int], existing_years: list,
) -> None:
    """
    §4 (الفقرة الثانية): تعديل نطاق الجيل بعد وجود Trim Model Years يُرفَض
    إذا أخرج أي سنة موجودة فعليًا خارج النطاق الجديد.
    """
    for year in existing_years:
        if new_start_year is not None and year < new_start_year:
            raise YearOutOfGenerationRangeError(
                f"لا يمكن تحديث بداية النطاق إلى {new_start_year}: توجد سنة موديل {year} مسجَّلة فعليًا أقل منها."
            )
        if new_end_year is not None and year > new_end_year:
            raise YearOutOfGenerationRangeError(
                f"لا يمكن تحديث نهاية النطاق إلى {new_end_year}: توجد سنة موديل {year} مسجَّلة فعليًا أكبر منها."
            )


# ---------------------------------------------------------------------------
# Approved VCT Design Baseline §6-9، 17: Market Availability (Batch 1)
# ---------------------------------------------------------------------------

@dataclass
class MarketAvailability:
    id: str
    country_ref_id: str          # إشارة عابرة للنطاق لـ ref.ref_values(country)؛ بلا FK فعلي
    trim_ref_id: Optional[str] = None
    trim_model_year_ref_id: Optional[str] = None


class InvalidMarketAvailabilityTargetError(Exception):
    """§6: يجب تحديد هدف واحد بالضبط — إما trim_ref_id أو trim_model_year_ref_id."""


class MarketAvailabilityLevelConflictError(Exception):
    """§8، §17: لا تعايش Trim-level وYear-specific Availability لنفس Trim."""


def validate_market_availability_target(
    trim_ref_id: Optional[str], trim_model_year_ref_id: Optional[str],
) -> None:
    """§6: Exactly One Target — ليس الاثنان معًا ولا كلاهما None."""
    has_trim = trim_ref_id is not None
    has_year = trim_model_year_ref_id is not None
    if has_trim == has_year:  # كلاهما True أو كلاهما False
        raise InvalidMarketAvailabilityTargetError(
            "يجب تحديد هدف واحد بالضبط: إما trim_ref_id أو trim_model_year_ref_id، لا كلاهما ولا لا شيء."
        )


def is_target_available_in_country(existing_rows_for_target: list, country_ref_id: str) -> bool:
    """
    §7: Whitelist Semantics — غياب أي صف للهدف = Global Availability (متاح
    في كل الأسواق). وجود صف واحد أو أكثر = Whitelist صارمة (متاح فقط في
    الدول المذكورة صراحة).
    """
    if not existing_rows_for_target:
        return True
    return any(row.country_ref_id == country_ref_id for row in existing_rows_for_target)


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


# ---------------------------------------------------------------------------
# Approved VCT Design Baseline §3-4: Trim Model Years — نقاط تجميع
# ---------------------------------------------------------------------------

def create_trim_model_year_via_repository(repository, trim_ref_id: str, year: int) -> TrimModelYear:
    """
    §4: تحل trim_ref_id → generation_id → (start_year, end_year)، ثم تتحقق،
    كل ذلك قبل الإدراج (نفس Transaction من جهة Repository في PostgreSQL؛
    التسلسل المنطقي هنا خالص لا يعرف تفاصيل القفل/الاتصال).
    """
    trim = repository.get_trim_by_id(trim_ref_id)
    if trim is None:
        raise TrimNotFoundError(f"لا توجد فئة سيارة بالمعرّف: {trim_ref_id}")

    generation_range = repository.get_generation_year_range_for_trim(trim_ref_id)
    start_year, end_year = generation_range if generation_range else (None, None)

    existing_years = repository.list_trim_model_years_for_trim(trim_ref_id)
    existing_year_values = [tmy.year for tmy in existing_years]

    tmy = create_trim_model_year(trim_ref_id, year, start_year, end_year, existing_year_values)
    return repository.insert_trim_model_year(tmy)


def update_generation_year_range_via_repository(
    repository, generation_id: str, start_year: Optional[int], end_year: Optional[int],
) -> Generation:
    """
    §4 (الفقرة الثانية): يرفض التحديث إن كان سيُخرج أي Trim Model Year
    موجودة فعليًا (عبر كل فئات هذا الجيل) خارج النطاق الجديد.
    """
    generation = repository.get_generation_by_id(generation_id)
    if generation is None:
        raise NotFoundError(f"لا يوجد جيل بالمعرّف: {generation_id}")
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError("start_year يجب ألا يتجاوز end_year.")

    existing_years = repository.list_trim_model_years_for_generation(generation_id)
    validate_generation_range_update_against_existing_years(start_year, end_year, existing_years)

    generation.start_year = start_year
    generation.end_year = end_year
    return repository.update_generation(generation)


# ---------------------------------------------------------------------------
# Approved VCT Design Baseline §6-9، 17: Market Availability — نقاط تجميع
# ---------------------------------------------------------------------------

def add_market_availability_via_repository(
    repository, country_ref_id: str,
    trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
) -> MarketAvailability:
    """
    §6-9، 17: التحقق من صحة الهدف هنا (منطق أعمال خالص)، ثم تُفوَّض عملية
    القفل + إعادة الفحص + الإدراج بالكامل لطبقة Repository (تفاصيل قفل
    Advisory وTransaction لا مكان لها في هذه الطبقة الخالصة) — نفس مبدأ
    فصل الطبقات المتَّبع في create_purchase_request_via_repository (CR-022)
    حيث يبقى منطق الأعمال الخالص أعلى من أي تفاصيل اتصال.
    """
    validate_market_availability_target(trim_ref_id, trim_model_year_ref_id)

    if trim_ref_id is not None and repository.get_trim_by_id(trim_ref_id) is None:
        raise TrimNotFoundError(f"لا توجد فئة سيارة بالمعرّف: {trim_ref_id}")
    if trim_model_year_ref_id is not None and repository.get_trim_model_year_by_id(trim_model_year_ref_id) is None:
        raise TrimNotFoundError(f"لا توجد سنة موديل بالمعرّف: {trim_model_year_ref_id}")

    return repository.insert_market_availability_with_lock(
        trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id, country_ref_id=country_ref_id,
    )


def is_trim_available_in_country_via_repository(
    repository, country_ref_id: Optional[str],
    trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
) -> bool:
    """
    §7، §18: نقطة القراءة الموحَّدة لدلالة Whitelist — تُستهلَك من Search
    (§18-19) وأي Read Path آخر يحتاج التحقق من إتاحة السوق. غياب
    country_ref_id (لم يُحدَّد سوق فعّال) يعني عدم تطبيق أي تصفية سوق هنا
    (نفس مبدأ REQ-SRC-006-E القائم في search_service.py).
    """
    if country_ref_id is None:
        return True
    validate_market_availability_target(trim_ref_id, trim_model_year_ref_id)
    existing_rows = repository.get_market_availability_for_target(
        trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id,
    )
    return is_target_available_in_country(existing_rows, country_ref_id)


# ---------------------------------------------------------------------------
# Batch 1 (Frontend Enablement): نقاط القراءة العامة (Public Browsing) لبناء
# قائمة اختيار السيارة Manufacturer→Model→Generation→Trim→Model Year. فجوة
# حقيقية اكتُشِفت أثناء التكامل مع الواجهة: لا مسارات قائمة كانت موجودة
# أصلًا (Create/Get بمعرّف فقط) — سدّها هنا ضروري لإتمام الرحلة المطلوبة،
# لا توسيع نطاق مستقل. طبقة تنسيق رقيقة فقط؛ كل منطق الحلّ (JOINs) في
# Repository، بنفس نمط list_my_purchase_requests_display_via_repository.
# ---------------------------------------------------------------------------

def list_approved_manufacturers_via_repository(repository) -> list:
    return repository.list_approved_manufacturers()


def list_approved_models_via_repository(repository, manufacturer_id: str) -> list:
    return repository.list_approved_models_for_manufacturer(manufacturer_id)


def list_generations_via_repository(repository, model_id: str) -> list:
    return repository.list_generations_for_model(model_id)


def list_trims_via_repository(repository, generation_id: str) -> list:
    return repository.list_trims_for_generation(generation_id)
