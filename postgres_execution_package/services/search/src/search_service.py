"""
search_service.py — منطق خدمة البحث (SRC)
المرجع: REQ-SRC-001..009 (يشمل REQ-SRC-006/006-A..E المعتمدة عبر CR-004)
        REQ-SRC-007, 007-A (سياسة الترتيب وكسر التعادل)
        SAD الحزمة 4، DD الحزمة 4 (خدمة ترتيب نتائج البحث — Application Service)

هذا الملف يُطبِّق منطق الأعمال المستقل عن قاعدة البيانات (وحدات اختبار خالصة)؛
لا اتصال فعلي بقاعدة بيانات هنا — التكامل مع str/pct/vct/ref الفعلي يتم في
طبقة الوصول للبيانات (Repository) في مرحلة لاحقة، خارج نطاق هذه الحزمة.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ---------------------------------------------------------------------------
# REQ-SRC-006-C, 006-D, 006-E: الاكتشاف التلقائي لدولة المستخدم (CR-004)
# ---------------------------------------------------------------------------

@dataclass
class EffectiveCountry:
    country_code: Optional[str]
    source: str  # account | geolocation | ip | manual | none


def determine_effective_country(
    account_country_code: Optional[str] = None,
    geolocation_country_code: Optional[str] = None,
    ip_country_code: Optional[str] = None,
    manual_country_code: Optional[str] = None,
) -> EffectiveCountry:
    """
    REQ-SRC-006-D: الاختيار اليدوي له الأولوية المطلقة فوق أي اكتشاف تلقائي.
    REQ-SRC-006-C: عند غياب الاختيار اليدوي، ترتيب الأولوية:
                   الحساب -> الموقع الجغرافي -> IP.
    REQ-SRC-006-E: عند تعذر الاكتشاف من كل المصادر، لا تصفية جغرافية (source=none).
    ملاحظة: هذه الدالة لا تُعدِّل أي بيانات حساب المستخدم بأي حال (فصل صريح
             بين جلسة البحث وبيانات الحساب، كما اعتُمد في CR-004).
    """
    if manual_country_code:
        return EffectiveCountry(country_code=manual_country_code, source="manual")
    if account_country_code:
        return EffectiveCountry(country_code=account_country_code, source="account")
    if geolocation_country_code:
        return EffectiveCountry(country_code=geolocation_country_code, source="geolocation")
    if ip_country_code:
        return EffectiveCountry(country_code=ip_country_code, source="ip")
    return EffectiveCountry(country_code=None, source="none")


# ---------------------------------------------------------------------------
# نموذج بيانات نتيجة بحث مبسَّط (Read Model — لا كيان أصيل، REQ-SRC-008)
# ---------------------------------------------------------------------------

@dataclass
class InventoryItemView:
    id: str
    business_code: str
    part_name: str
    store_name: str
    store_ref_id: str
    country_code: str
    city_code: str
    condition_code: str
    price_amount: Optional[float]
    is_verified_seller: bool
    seller_rating: Optional[float]
    created_at: datetime


# ---------------------------------------------------------------------------
# REQ-SRC-003, 004, 006, 006-A, 006-B: تطبيق معايير التصفية
# ---------------------------------------------------------------------------

def apply_search_filters(
    items: List[InventoryItemView],
    country_code: Optional[str] = None,
    city_code: Optional[str] = None,
    price_filter: str = "all",          # all | priced_only | unpriced_only  (REQ-SRC-004)
    condition_code: Optional[str] = None,  # REQ-SRC-006-A
    verified_sellers_only: bool = False,   # REQ-SRC-006-B
    store_ref_id: Optional[str] = None,    # سياق صفحة المتجر: بحث مقصور على متجر واحد (إعادة استخدام المكوّن نفسه)
) -> List[InventoryItemView]:
    result = items

    # REQ-SRC-006/006-E: تصفية جغرافية فقط إذا وُجدت دولة فعّالة؛ لا قيد إن غابت
    if country_code:
        result = [i for i in result if i.country_code == country_code]
    if city_code:
        result = [i for i in result if i.city_code == city_code]

    # سياق صفحة المتجر: نفس خدمة/مكوّن البحث، مع فلتر المتجر مطبَّقًا تلقائيًا
    if store_ref_id:
        result = [i for i in result if i.store_ref_id == store_ref_id]

    if price_filter == "priced_only":
        result = [i for i in result if i.price_amount is not None]
    elif price_filter == "unpriced_only":
        result = [i for i in result if i.price_amount is None]
    # price_filter == "all": لا تصفية (REQ-SRC-004)

    if condition_code:
        result = [i for i in result if i.condition_code == condition_code]

    if verified_sellers_only:
        result = [i for i in result if i.is_verified_seller]

    return result


# ---------------------------------------------------------------------------
# REQ-SRC-007, 007-A: الترتيب مع كسر تعادل ثابت
# ---------------------------------------------------------------------------

def sort_results(items: List[InventoryItemView]) -> List[InventoryItemView]:
    """
    سياسة ترتيب افتراضية مبسّطة لهذا الإصدار: تقييم البائع تنازليًا (الأعلى أولاً)،
    مع القيم المفقودة (None) في آخر الترتيب.
    REQ-SRC-007-A: كسر التعادل الثابت عند تساوي معيار الترتيب:
                   الأحدث إنشاءً أولاً (created_at تنازليًا)،
                   ثم معرّف الأعمال تصاعديًا كفاصل نهائي.
    """
    def sort_key(item: InventoryItemView):
        rating_for_sort = -(item.seller_rating if item.seller_rating is not None else -1)
        return (rating_for_sort, -item.created_at.timestamp(), item.business_code)

    return sorted(items, key=sort_key)


# ---------------------------------------------------------------------------
# تقسيم الصفحات (Pagination) — DD الحزمة 2، القسم 2.3
# ---------------------------------------------------------------------------

@dataclass
class PageResult:
    items: List[InventoryItemView]
    page: int
    page_size: int
    total_items: int


def paginate(items: List[InventoryItemView], page: int = 1, page_size: int = 20) -> PageResult:
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    start = (page - 1) * page_size
    end = start + page_size
    return PageResult(
        items=items[start:end],
        page=page,
        page_size=page_size,
        total_items=len(items),
    )


# ---------------------------------------------------------------------------
# نقطة تجميع تعتمد على طبقة Repository (فصل منطق الأعمال عن الوصول للبيانات)
# ---------------------------------------------------------------------------

def execute_search_via_repository(
    repository,  # SearchRepository؛ لا استيراد مباشر هنا لتفادي اعتمادية دائرية
    account_country_code: Optional[str] = None,
    geolocation_country_code: Optional[str] = None,
    ip_country_code: Optional[str] = None,
    manual_country_code: Optional[str] = None,
    trim_ref_id: Optional[str] = None,
    city_code: Optional[str] = None,
    price_filter: str = "all",
    condition_code: Optional[str] = None,
    verified_sellers_only: bool = False,
    store_ref_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    نقطة الدخول الفعلية المستخدَمة في التنفيذ الحقيقي: تحدِّد الدولة الفعّالة
    (منطق أعمال خالص)، ثم تستدعي المستودع (Repository) لجلب المرشَّحين عبر
    استعلام مفهرَس، ثم تطبِّق بقية قواعد العمل (السعر، البائعون الموثَّقون،
    الترتيب، التقسيم) على النتيجة — دون أن تعرف شيئًا عن تفاصيل قاعدة البيانات.
    """
    effective = determine_effective_country(
        account_country_code, geolocation_country_code, ip_country_code, manual_country_code
    )

    candidates = repository.fetch_matching_items(
        trim_ref_id=trim_ref_id,
        condition_ref_id=condition_code,
        country_ref_id=effective.country_code,
        city_ref_id=city_code,
        store_ref_id=store_ref_id,
    )

    # تصفية السعر والبائعين الموثَّقين تبقى على مستوى منطق الأعمال (لا الاستعلام المفهرَس)
    filtered = apply_search_filters(
        candidates,
        price_filter=price_filter,
        verified_sellers_only=verified_sellers_only,
    )
    sorted_items = sort_results(filtered)
    page_result = paginate(sorted_items, page, page_size)

    return {
        "results": page_result.items,
        "effective_country_code": effective.country_code,
        "effective_country_source": effective.source,
        "pagination": {
            "page": page_result.page,
            "page_size": page_result.page_size,
            "total_items": page_result.total_items,
        },
    }


# ---------------------------------------------------------------------------
# نقطة التجميع: تنفيذ رحلة البحث الكاملة (بلا وصول فعلي لقاعدة بيانات)
# ---------------------------------------------------------------------------

def execute_search(
    items: List[InventoryItemView],
    account_country_code: Optional[str] = None,
    geolocation_country_code: Optional[str] = None,
    ip_country_code: Optional[str] = None,
    manual_country_code: Optional[str] = None,
    city_code: Optional[str] = None,
    price_filter: str = "all",
    condition_code: Optional[str] = None,
    verified_sellers_only: bool = False,
    store_ref_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    effective = determine_effective_country(
        account_country_code, geolocation_country_code, ip_country_code, manual_country_code
    )
    filtered = apply_search_filters(
        items,
        country_code=effective.country_code,
        city_code=city_code,
        price_filter=price_filter,
        condition_code=condition_code,
        verified_sellers_only=verified_sellers_only,
        store_ref_id=store_ref_id,
    )
    sorted_items = sort_results(filtered)
    page_result = paginate(sorted_items, page, page_size)

    return {
        "results": page_result.items,
        "effective_country_code": effective.country_code,
        "effective_country_source": effective.source,
        "pagination": {
            "page": page_result.page,
            "page_size": page_result.page_size,
            "total_items": page_result.total_items,
        },
    }
