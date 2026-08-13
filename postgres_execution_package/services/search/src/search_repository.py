"""
search_repository.py — طبقة الوصول للبيانات لخدمة البحث (Repository Pattern)
المرجع: توصية المالك — فصل منطق الأعمال (search_service.py) عن الوصول لقاعدة
البيانات؛ DD الحزمة 1 (المخطط الفيزيائي)، الحزمة التنفيذية الأولى (DDL).

يحدِّد هذا الملف واجهة تجريدية (SearchRepository) لا تعرف عنها search_service.py
شيئًا سوى العقد نفسه (Dependency Inversion)؛ التنفيذ الفعلي عبر PostgreSQL
(PostgresSearchRepository) منفصل تمامًا، وتنفيذ وهمي في الذاكرة
(InMemorySearchRepository) يُستخدَم للاختبار دون الحاجة لقاعدة بيانات حقيقية.

مسؤولية الفصل بين الطبقتين:
- Repository: يجلب المرشَّحين عبر استعلامات مفهرَسة (الوحدة، الحالة، القطعة،
  المتجر، الدولة/المدينة) — أي تصفية تعتمد على أعمدة مفهرَسة فعليًا في DDL.
- Service (search_service.py): يطبِّق بقية قواعد العمل (تصنيف السعر الثلاثي،
  البائعون الموثَّقون، الترتيب وكسر التعادل، التقسيم) على النتيجة المُرجَعة.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from search_service import InventoryItemView, matches_search_query


class SearchRepository(ABC):
    """العقد الذي تعتمد عليه خدمة البحث؛ لا تفاصيل تنفيذ هنا."""

    @abstractmethod
    def fetch_matching_items(
        self,
        q: Optional[str] = None,
        trim_ref_id: Optional[str] = None,
        year: Optional[int] = None,
        condition_ref_id: Optional[str] = None,
        country_ref_id: Optional[str] = None,
        city_ref_id: Optional[str] = None,
        store_ref_id: Optional[str] = None,
    ) -> List[InventoryItemView]:
        """يُرجِع عناصر المخزون النشطة المطابقة للمعايير المفهرَسة فقط؛
        لا يطبِّق تصفية السعر أو البائعين الموثَّقين أو الترتيب أو التقسيم —
        تلك مسؤولية search_service.py. CR-020: q يُطابَق داخل هذه الطبقة
        (Exact/Prefix عبر كل أسماء القطعة الأربعة)، لأنه يستوجب وصولًا
        مباشرًا لجدول pct.localized_names لا يتوفر بعد الجلب.
        Approved VCT Design Baseline §18: year (بلا trim_ref_id لا معنى له)
        يُضيِّق المطابقة إلى General أو Year-specific لنفس (trim, year)
        بالضبط؛ trim_ref_id بلا year يعني General أو أي Year-specific تحت
        نفس الفئة، بغضّ النظر عن السنة."""
        raise NotImplementedError


class PostgresSearchRepository(SearchRepository):
    """
    تنفيذ فعلي عبر PostgreSQL. يفترض وجود اتصال (connection) مُدار خارجيًا
    (Connection Pool) يُمرَّر عند الإنشاء؛ لا تُدار دورة حياة الاتصال هنا.

    ملاحظة أمانة: هذا الكود مكتوب بصياغة SQL صحيحة نحويًا استنادًا لمخطط
    الحزمة التنفيذية الأولى (str.inventory_items، pct.catalog_parts،
    pct.localized_names، str.stores)، لكن لم يُختبَر فعليًا على اتصال حي
    داخل هذه البيئة (لا اتصال شبكي لتثبيت PostgreSQL هنا)؛ يستوجب اختبار
    تكامل فعلي على بيئة حقيقية قبل الاعتماد النهائي.
    """

    # CR-020 v1: تطبيع عربي "آمن" مطابق حرفيًا لـnormalize_arabic_search_text
    # في search_service.py (توحيد أشكال الألف، إزالة التشكيل/التطويل، توحيد
    # المسافات، lower() للأحرف اللاتينية) — بلا pg_trgm ولا فهرسة GIN ولا
    # Fuzzy Matching، بقرار v1 المعتمَد صراحة. Sequential scan على
    # localized_names مقبول لهذا الإصدار (النطاق مُقيَّد صراحة بعدم إضافة
    # فهرسة جديدة الآن).
    _NORMALIZE_SQL = (
        "lower(regexp_replace(regexp_replace(translate({expr}, 'أإآٱ', 'اااا'), "
        "'[\u064b-\u0652\u0670\u0640]', '', 'g'), '\\s+', ' ', 'g'))"
    )

    def __init__(self, connection):
        self._connection = connection

    def fetch_matching_items(
        self,
        q: Optional[str] = None,
        trim_ref_id: Optional[str] = None,
        year: Optional[int] = None,
        condition_ref_id: Optional[str] = None,
        country_ref_id: Optional[str] = None,
        city_ref_id: Optional[str] = None,
        store_ref_id: Optional[str] = None,
    ) -> List[InventoryItemView]:
        # يعتمد على: idx_inventory_items_status، idx_inventory_items_store_id،
        # idx_inventory_items_part، idx_stores_status، idx_stores_country،
        # idx_stores_city (017_add_store_location.sql)، idx_compatibility_trim.
        # مطابقة q (CR-020) عبر Sequential Scan على pct.localized_names —
        # لا فهرس جديد، بقرار نطاق v1 المعتمَد.
        normalize_col = self._NORMALIZE_SQL.format(expr="qn.name_value")
        normalize_param = self._NORMALIZE_SQL.format(expr="%(q)s")
        query = f"""
            SELECT
                ii.id, ii.business_code, pl.name_value AS part_name,
                st.id AS store_ref_id, ii.condition_ref_id,
                st.country_ref_id AS country_code, st.city_ref_id AS city_code,
                ii.price_amount, ii.created_at
            FROM str.inventory_items ii
            JOIN str.stores st ON st.id = ii.store_id
            JOIN pct.catalog_parts pp ON pp.id = ii.catalog_part_ref_id
            JOIN pct.localized_names pl ON pl.catalog_part_id = pp.id AND pl.name_kind = 'canonical'
            WHERE ii.status IN ('active', 'out_of_stock')
              AND st.status = 'active'
              AND (%(trim_ref_id)s IS NULL OR EXISTS (
                    -- Approved VCT Design Baseline §18: بلا سنة → General أو أي
                    -- Year-specific تحت نفس Trim. بسنة → General أو Year-specific
                    -- لنفس (Trim, Year) بالضبط فقط.
                    SELECT 1 FROM cmp.compatibility_records cr
                    WHERE cr.catalog_part_ref_id = pp.id AND cr.status = 'active'
                      AND (
                        cr.trim_ref_id = %(trim_ref_id)s
                        OR (
                          %(year)s IS NULL AND cr.trim_model_year_ref_id IN (
                              SELECT id FROM vct.trim_model_years WHERE trim_ref_id = %(trim_ref_id)s
                          )
                        )
                        OR (
                          %(year)s IS NOT NULL AND cr.trim_model_year_ref_id IN (
                              SELECT id FROM vct.trim_model_years
                              WHERE trim_ref_id = %(trim_ref_id)s AND year = %(year)s
                          )
                        )
                      )
              ))
              AND (%(condition_ref_id)s IS NULL OR ii.condition_ref_id = %(condition_ref_id)s)
              AND (%(country_ref_id)s IS NULL OR st.country_ref_id = %(country_ref_id)s)
              AND (%(city_ref_id)s IS NULL OR st.city_ref_id = %(city_ref_id)s)
              AND (%(store_ref_id)s IS NULL OR st.id = %(store_ref_id)s)
              AND (%(q)s IS NULL OR EXISTS (
                    -- CR-020: مطابقة عبر الأسماء الأربعة (canonical/local/english/synonym)
                    SELECT 1 FROM pct.localized_names qn
                    WHERE qn.catalog_part_id = pp.id
                      AND qn.name_kind IN ('canonical', 'local', 'english', 'synonym')
                      AND ({normalize_col} = {normalize_param}
                           OR {normalize_col} LIKE {normalize_param} || '%%')
              ))
        """
        params = {
            "q": q if q else None,
            "trim_ref_id": trim_ref_id,
            "year": year,
            "condition_ref_id": condition_ref_id,
            "country_ref_id": country_ref_id,
            "city_ref_id": city_ref_id,
            "store_ref_id": store_ref_id,
        }
        with self._connection.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        # تعيين (Mapping) الصفوف الخام إلى نموذج القراءة الموحَّد؛ حقول إضافية
        # (is_verified_seller، seller_rating) تُستكمَل من استعلامات/خدمات
        # منفصلة في تنفيذ فعلي لاحق، محذوفة هنا لتبسيط العرض.
        return [
            InventoryItemView(
                id=row["id"], business_code=row["business_code"], part_name=row["part_name"],
                store_name="", store_ref_id=row["store_ref_id"],
                country_code=row["country_code"], city_code=row["city_code"],
                condition_code=row["condition_ref_id"], price_amount=row["price_amount"],
                is_verified_seller=False, seller_rating=None, created_at=row["created_at"],
            )
            for row in rows
        ]


class InMemorySearchRepository(SearchRepository):
    """
    تنفيذ وهمي للاختبار فقط؛ يحاكي سلوك الاستعلام المفهرَس دون قاعدة بيانات
    حقيقية. CR-020: مطابقة q هنا تتم مقابل part_name فقط (الاسم المعروض
    الوحيد المتاح في InventoryItemView المبسَّط لهذا التنفيذ الوهمي) — لا
    محاكاة لأسماء local/english/synonym المنفصلة كما في PostgreSQL الحقيقي؛
    نفس القيد الموثَّق أصلًا لـtrim_ref_id في هذا الصنف. اختبار المطابقة عبر
    الأنواع الأربعة الفعلية يقع على عاتق اختبار التكامل الحي فقط.
    """

    def __init__(self, items: List[InventoryItemView]):
        self._items = items

    def fetch_matching_items(
        self,
        q: Optional[str] = None,
        trim_ref_id: Optional[str] = None,
        year: Optional[int] = None,
        condition_ref_id: Optional[str] = None,
        country_ref_id: Optional[str] = None,
        city_ref_id: Optional[str] = None,
        store_ref_id: Optional[str] = None,
    ) -> List[InventoryItemView]:
        result = self._items
        if q:
            result = [i for i in result if matches_search_query(i.part_name, q)]
        if condition_ref_id:
            result = [i for i in result if i.condition_code == condition_ref_id]
        if country_ref_id:
            result = [i for i in result if i.country_code == country_ref_id]
        if city_ref_id:
            result = [i for i in result if i.city_code == city_ref_id]
        if store_ref_id:
            result = [i for i in result if i.store_ref_id == store_ref_id]
        # trim_ref_id/year يستوجبان بيانات توافق (cmp/vct) غير متوفرة في هذا
        # التنفيذ الوهمي المبسَّط؛ يُهمَلان هنا عمدًا (نفس القيد الموثَّق أصلًا
        # لـtrim_ref_id قبل هذه الدفعة) — تُختبَر دلالة §18 حصرًا على PostgreSQL حي.
        return result
