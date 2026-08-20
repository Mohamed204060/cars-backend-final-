"""
search_api.py — طبقة REST API لخدمة البحث (SRC)
المرجع: api_spec/openapi.yaml — GET /search/parts (موثَّق بالكامل أصلاً ضمن
        الشريحة الأولى المعتمَدة؛ لا حاجة لامتداد عقد جديد هنا، التنفيذ فقط).

ملاحظتا نطاق (لا تُخفيان، موثَّقتان صراحة):
1. CR-020 (v1): معامل q (نص حر) أصبح مُنفَّذًا الآن — مطابقة Exact + Prefix
   فقط (بعد تطبيع عربي آمن: توحيد أشكال الألف، إزالة التشكيل/التطويل) عبر
   الأسماء الأربعة لكل قطعة (canonical/local/english/synonym). بلا
   pg_trgm ولا فهرسة GIN ولا Fuzzy Matching — مُقيَّد صراحةً بقرار نطاق v1
   المعتمَد؛ راجع search_service.normalize_arabic_search_text وَ
   PostgresSearchRepository._NORMALIZE_SQL للتفاصيل. معامل sort (ترتيب
   مخصَّص) يبقى مقبولًا في الطلب بلا أي أثر (فجوة مستقلة، خارج نطاق CR-020
   كما وُصِف).
2. account_country_code/geolocation_country_code/ip_country_code (REQ-SRC-006-C)
   تتطلب مصادر بيانات غير متوفرة بعد (حساب المستخدم، تحديد موقع، قاعدة IP)؛
   يُستخدَم country_ref_id المُرسَل من العميل مباشرة كـmanual_country_code
   فقط (المصدر الوحيد الفعلي المتاح حاليًا عبر العقد).
3. image_url ليس له أي تخزين أو منطق في الكود؛ يُعاد null دائمًا (لا نظام
   صور مبني بعد؛ GAP-B مسجَّلة مستقلة). price_display_text يُشتَق محليًا
   هنا (REQ-STR-014).
4. CR-019: store_id أصبح حقلًا حقيقيًا في SearchResultItem الآن — كان
   مجلوبًا فعليًا في InventoryItemView.store_ref_id منذ البداية لكن غير
   معروض؛ لا تغيير على أي استعلام SQL، إضافة Mapping فقط.
"""

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from auth_api import get_correlation_id, SESSION_COOKIE_NAME
from search_service import execute_search_via_repository, normalize_arabic_search_text
from ana_service import record_analytics_event_via_repository
from session_service import Session, ensure_session_valid, hash_token

router = APIRouter(prefix="/api/v1", tags=["search"])

_MAX_NORMALIZED_TERM_LENGTH = 100  # Pre-Gate Corrective #3: حد دفاعي إضافي، Data Minimization


class SearchResultItem(BaseModel):
    inventory_item_id: str
    business_code: str
    part_name: str
    store_id: str
    store_name: str
    image_url: Optional[str] = None
    price_amount: Optional[float] = None
    price_display_text: str


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    effective_country_code: Optional[str] = None
    effective_country_source: str
    pagination: PaginationMeta


def get_search_repository(request: Request):
    return request.app.state.search_repository


def _get_ana_repository_if_wired(request: Request):
    """Batch 3A Slice 2 (Search Analytics): تسجيل تحليلي اختياري بحت — لا يجوز
    أن يكسر البحث لو لم يُوصَّل ana_repository (وحدات/بيئات لا تحتاجه إطلاقًا،
    بما فيها test_search_api.py/test_postgres_search_api_integration.py
    الحاليتان المغلقتان، بلا أي تعديل عليهما). getattr بدل الوصول المباشر
    عمدًا — خلافًا لـget_ana_repository في ana_api.py (اعتماد إلزامي هناك،
    مناسب لأن ana هو صاحب ذلك الـEndpoint؛ هنا search هو المالك، وana ضيف
    اختياري تمامًا)."""
    return getattr(request.app.state, "ana_repository", None)


def _get_optional_actor_ref_id_if_wired(request: Request) -> Optional[str]:
    """Batch 3A Slice 2 (توجيه صريح: لا نجعل غياب Actor قرارًا دائمًا فقط
    لتفادي تعديل اختبارات مغلقة) — نسخة آمنة بحتة من auth_api.get_optional_session
    لا تفرض Depends(get_session_repository) (الذي كان سيكسر test_search_api.py
    فورًا لو استُخدِم إلزاميًا). إن وُجدت جلسة صالحة فعليًا (session_repository
    موصول + Cookie صالح)، actor_ref_id يُشتَق منها لقيمة تحليلية أدق؛ غيابها
    (بيئة/اختبار لا يحتاجها) لا يفرض شيئًا — البحث يبقى Anonymous-safe دائمًا،
    نفس عقده الأصلي بلا أي تغيير في Authorization."""
    session_repo = getattr(request.app.state, "session_repository", None)
    if session_repo is None:
        return None
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None
    try:
        live_session = session_repo.get_active_session_by_token_hash(hash_token(session_id))
        valid_session: Session = ensure_session_valid(live_session, datetime.now(timezone.utc))
        return valid_session.user_id
    except Exception:
        return None


def _record_search_event_best_effort(ana_repo, event_type: str, actor_ref_id: Optional[str],
                                      correlation_id: str, metadata: dict) -> None:
    """أي فشل هنا (Repository غير موصول، أو خطأ DB عابر) لا يجوز أن يُسقِط
    استجابة البحث نفسها — تسجيل تحليلي، ليس جزءًا من الوظيفة الأساسية."""
    if ana_repo is None:
        return
    try:
        record_analytics_event_via_repository(
            ana_repo, event_type=event_type, actor_ref_id=actor_ref_id,
            context_type="search", correlation_id=correlation_id, metadata=metadata,
        )
    except Exception:
        pass


def _price_display_text(price_amount: Optional[float]) -> str:
    """REQ-STR-014: نص بديل عند غياب السعر."""
    if price_amount is None:
        return "تواصل مع البائع للسعر"
    return f"{price_amount:.2f}"


@router.get("/search/parts", response_model=SearchResponse)
def search_parts(
    q: Optional[str] = Query(default=None),
    trim_ref_id: Optional[str] = Query(default=None),
    year: Optional[int] = Query(default=None),
    price_filter: str = Query(default="all"),
    condition_ref_id: Optional[str] = Query(default=None),
    verified_sellers_only: bool = Query(default=False),
    country_ref_id: Optional[str] = Query(default=None),
    city_ref_id: Optional[str] = Query(default=None),
    store_ref_id: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    sort: Optional[str] = Query(default=None),
    correlation_id: str = Depends(get_correlation_id),
    search_repo=Depends(get_search_repository),
    ana_repo=Depends(_get_ana_repository_if_wired),
    actor_ref_id=Depends(_get_optional_actor_ref_id_if_wired),
):
    result = execute_search_via_repository(
        search_repo,
        manual_country_code=country_ref_id,
        q=q,
        trim_ref_id=trim_ref_id,
        year=year,
        city_code=city_ref_id,
        price_filter=price_filter,
        condition_code=condition_ref_id,
        verified_sellers_only=verified_sellers_only,
        store_ref_id=store_ref_id,
        page=page,
        page_size=page_size,
    )

    items = [
        SearchResultItem(
            inventory_item_id=i.id, business_code=i.business_code, part_name=i.part_name,
            store_id=i.store_ref_id, store_name=i.store_name, image_url=None,
            price_amount=i.price_amount, price_display_text=_price_display_text(i.price_amount),
        )
        for i in result["results"]
    ]

    # Batch 3A Slice 2 — Search Analytics (§32 event types، بلا حقل جديد):
    # search_performed دائمًا، search_zero_results فقط عند غياب النتائج تمامًا.
    # actor_ref_id يُشتَق من جلسة حقيقية صالحة إن وُجدت (_get_optional_actor_ref_id_if_wired
    # أعلاه) — نسخة آمنة بحتة، لا تفرض Depends(get_session_repository) إلزاميًا
    # (كانت ستكسر test_search_api.py/test_postgres_search_api_integration.py
    # المغلقتين فورًا، نفس Pattern المكتشَف مسبقًا مع ref_repository). البحث
    # يبقى Anonymous-safe بلا أي تغيير في Authorization — actor_ref_id إثراء
    # تحليلي اختياري فقط، لا شرط.
    #
    # Pre-Gate Corrective #3: normalized_query_term — ليس q الخام. نفس
    # normalize_arabic_search_text المستخدَمة فعليًا في search_service.py
    # لمطابقة اسم القطعة (لا منطق جديد، استدعاء مباشر لدالة نقية موجودة
    # أصلًا). التطبيع يزيل التشكيل/التطويل ويوحِّد أشكال الألف — نص بحث عن
    # قطعة غيار مُطبَّع، لا بيانات شخصية حرة. حد طول إضافي دفاعي (100 حرف)
    # فوق ذلك. يُسجَّل فقط عند وجود نص بحث فعلي (q)، بلا استثناء لبحث
    # المركبة فقط (trim_ref_id يبقى المفتاح الوحيد في تلك الحالة).
    normalized_term = normalize_arabic_search_text(q)[:_MAX_NORMALIZED_TERM_LENGTH] if q else None
    search_metadata = {
        "has_query_text": bool(q), "query_length": len(q) if q else 0,
        "trim_ref_id": trim_ref_id, "results_count": len(items),
        "normalized_query_term": normalized_term,
    }
    _record_search_event_best_effort(ana_repo, "search_performed", actor_ref_id, correlation_id, search_metadata)
    if len(items) == 0:
        _record_search_event_best_effort(ana_repo, "search_zero_results", actor_ref_id, correlation_id, search_metadata)

    return SearchResponse(
        results=items,
        effective_country_code=result["effective_country_code"],
        effective_country_source=result["effective_country_source"],
        pagination=PaginationMeta(**result["pagination"]),
    )
