"""
rpt_service.py — طبقة التنسيق لتقارير الإدارة (Batch 3A Slice 2)

===========================================================================
Reporting Data Dictionary — Executive Dashboard + Marketplace Conversion
===========================================================================
كل Metric هنا مُعرَّف مرة واحدة فقط (SSOT) — أي استهلاك مستقبلي له (Export،
Regulatory Report، إلخ) يجب أن يستدعي rpt_repository.get_executive_dashboard
نفسها، لا إعادة حساب الصيغة في مكان آخر.

--- users_total ---
Formula: COUNT(*) FROM iam.users
Included states: كل الحالات (active/suspended/banned/archived)
Date semantics: Snapshot لحظة الاستعلام
Filters: لا شيء

--- users_new ---
Formula: COUNT(*) FROM iam.users WHERE created_at BETWEEN date_from AND date_to
Included states: كل الحالات
Date semantics: يتطلب date_from و/أو date_to صراحة؛ 0 بلا مدى (لا معنى لـ"جدد" بلا نافذة زمنية)
Null handling: date_from/date_to اختياريان كل على حدة (>= أو <= فقط إن وُجد أحدهما)

--- users_by_status / sellers_total ---
Formula: GROUP BY status | primary_role IN ('individual_seller','business_seller')
ملاحظة: sellers_total يُحسَب من iam.users.primary_role (الدور)، وليس من عدد
المتاجر (بائع واحد قد يملك متجرًا واحدًا كحد أقصى وفق REQ-STR الحالية، لكن
هذا Metric يقيس المستخدمين ذوي دور بائع بصرف النظر عن امتلاكهم متجرًا فعليًا).

--- stores_total / stores_by_status ---
Formula: GROUP BY status FROM str.stores (creating/active/suspended/archived)
Date semantics: Snapshot

--- inventory_items_total / inventory_items_by_status ---
Formula: GROUP BY status FROM str.inventory_items (active/out_of_stock/hidden/archived)
Date semantics: Snapshot

--- catalog_parts_total / catalog_parts_by_status ---
Formula: GROUP BY status FROM pct.catalog_parts (proposed/approved/archived)
Date semantics: Snapshot

--- purchase_requests_total / purchase_requests_by_status ---
Formula: GROUP BY status FROM pur.purchase_requests (open/under_review/fulfilled/expired/cancelled)
Date semantics: Snapshot

--- purchase_requests_without_offers ---
Formula: COUNT(pr) WHERE NOT EXISTS (SELECT 1 FROM pur.offers WHERE purchase_request_id = pr.id)
Included states: كل حالات الطلب (بغضّ النظر عن status، فقط غياب أي عرض)

--- offers_total / offers_by_status ---
Formula: GROUP BY status FROM pur.offers (submitted/accepted/rejected/withdrawn/expired)

--- request_to_offer_rate ---
Formula: (purchase_requests_total - purchase_requests_without_offers) / purchase_requests_total
Null handling: 0.0 إذا purchase_requests_total = 0 (Division-by-zero محروسة)
Sensitivity: Marketplace Matching & Conversion — ليس Financial Metric (§7 من الكتالوج)

--- request_to_accepted_offer_rate ---
Formula: purchase_requests_by_status['fulfilled'] / purchase_requests_total
Null handling: 0.0 إذا purchase_requests_total = 0
ملاحظة حاكمة صريحة: Accepted Offer ≠ Completed Financial Sale — هذا Metric
يقيس معدّل التوفيق (Matching) داخل السوق فقط، لا مبيعات مالية فعلية (لا
Payment Gateway في V1).

--- avg_offers_per_request ---
Formula: offers_total / purchase_requests_total
Null handling: 0.0 إذا purchase_requests_total = 0

--- subscriptions_active_total / subscriptions_by_plan ---
Formula: GROUP BY ref.ref_values.code FROM sub.seller_subscriptions WHERE status='active'
         JOIN sub.plans JOIN ref.ref_values
Date semantics: Snapshot (الاشتراكات النشطة الآن، لا يُفلتَر بالمدى الزمني)

===========================================================================
امتداد Slice 2 — Search Analytics + Missing Parts/Unmet Demand
===========================================================================

--- search_volume / zero_result_count / zero_result_rate ---
SSOT: ana.events (event_type='search_performed'|'search_zero_results')
Formula: COUNT لكل نوع ضمن المدى الزمني؛ zero_result_rate = zero/performed (0.0 إذا performed=0)
Date semantics: يتطلب Instrumentation فعلي (بدأ فعليًا من هذه الدفعة في search_api.py)
                — لا بيانات قبل تاريخ الربط الفعلي، لا Backfill.
Null handling: date_from/date_to اختياريان؛ بلا مدى = كل الأحداث المسجَّلة تاريخيًا منذ Instrumentation.

--- top_zero_result_vehicles ---
Formula: GROUP BY metadata->>'trim_ref_id' لأحداث search_zero_results، أعلى 20 مرتَّبة تنازليًا
Filters: date_from/date_to نفس أعلاه
Sensitivity: Internal — trim_ref_id فقط (لا نص بحث حر مخزَّن، Data Minimization)

--- zero_result_search_count / purchase_requests_without_offers_count (Missing Parts) ---
SSOT: ana.events (zero_result_search_count) + pur.purchase_requests/pur.offers
      (purchase_requests_without_offers_count — نفس تعريف purchase_requests_without_offers
      في Executive Dashboard أعلاه، لا صيغة ثانية لنفس المعنى)
Date semantics: zero_result_search_count يُفلتَر بالمدى (نشاط)؛
                purchase_requests_without_offers_count دائمًا Snapshot (حالة حالية، بلا فلترة)

--- top_unmet_demand_parts ---
Formula: GROUP BY catalog_part_ref_id لطلبات بلا عروض، أعلى 20 مرتَّبة تنازليًا
Included states: كل حالات الطلب (بغضّ النظر عن status، فقط غياب أي عرض) — Snapshot، بلا فلترة زمنية
Sensitivity: Internal — معرِّفات القطع فقط، لا حل أسماء هنا (Foundation خفيفة عمدًا)

--- top_missing_search_terms (Search Analytics + Missing Parts) — Pre-Gate Corrective #3 ---
SSOT: ana.events (metadata->>'normalized_query_term')
Formula: GROUP BY metadata->>'normalized_query_term' لأحداث search_zero_results حيث
         النص غير NULL، أعلى 20 مرتَّبة تنازليًا
Date semantics: يُفلتَر بـdate_from/date_to (نفس فلترة zero_result_count تمامًا،
                نفس المصدر ana.events.occurred_at_utc)
Null handling: بحث بالمركبة فقط (بلا q) لا يُنتِج normalized_query_term — يظهر
               بدلًا منه في top_zero_result_vehicles حصرًا
Sensitivity: Internal — normalized_query_term نص مُطبَّع (normalize_arabic_search_text
             الموجودة أصلًا في search_service.py للمطابقة، ليس q الخام) بحد أقصى 100
             حرف؛ لا بيانات شخصية حرة (بريد/هاتف/اسم) — نص بحث عن قطعة غيار فقط.
             أُضيف هذا الحقل تحديدًا ليستطيع Missing Parts الإجابة عمليًا عن "ما
             القطعة التي يبحث عنها المستخدمون ولا يجدونها؟" بدل عدّ إجمالي فقط.
===========================================================================

===========================================================================
امتداد Slice 2 (تابع) — Marketplace Intelligence + Trending Parts
===========================================================================
لا Endpoint/جدول جديد — تركيب فوق المؤشرات أعلاه فقط (إعادة استخدام كاملة،
لا حساب مزدوج لأي صيغة).

--- demand_hotspot_vehicles / unmet_demand_parts (Marketplace Intelligence) ---
SSOT: نفس top_zero_result_vehicles (Search Analytics) وnفس top_unmet_demand_parts
      (Missing Parts) حرفيًا — استدعاء مباشر، لا إعادة تعريف.

--- catalog_parts_with_no_active_supply --- (Pre-Gate Corrective #2؛ الاسم السابق
    كان catalog_parts_with_zero_inventory — أُعيد تسميته لأن الصيغة القديمة كانت
    تُدرِج out_of_stock ضمن "لديه Supply"، بينما لا شيء منه قابل للشراء فعليًا الآن)
Formula: COUNT(pct.catalog_parts WHERE status='approved' AND NOT EXISTS
         (str.inventory_items WHERE catalog_part_ref_id=cp.id AND status='active'))
تعريف "Supply Gap" محسوم الآن: قطعة معتمَدة، لا عنصر مخزون واحد قابل للشراء
الآن فعليًا (status='active' حصرًا — out_of_stock/hidden/archived جميعها
مُستبعَدة من "Supply حقيقية"، رغم أن out_of_stock قد يظهر في نتائج البحث
للتصفح فقط حسب search_repository.py؛ الفرق مقصود: هذا المؤشر يقيس فجوة عرض
فعلية للإدارة، لا مجرد قابلية الظهور). Snapshot، بلا فلترة زمنية.

--- sellers_to_active_stores_ratio --- (Pre-Gate Corrective #1؛ الاسم السابق
    كان seller_to_store_ratio والمقام كان كل المتاجر بلا استثناء status)
Formula: sellers_total / COUNT(str.stores WHERE status='active') — 0.0 إذا لا متاجر نشطة
تفسير: المقام active حصرًا لأن متجرًا مُعلَّقًا/مؤرشَفًا لا يمثّل تغطية عرض
فعلية للمشتري رغم وجود صفّه في الجدول. > 1 يعني بائعين مسجَّلين بلا متجر
نشط فعلي بعد (فجوة تفعيل)؛ ≈ 1 تغطية متوازنة تقريبًا. مؤشر تقريبي بسيط —
ليس نموذج تغطية جغرافي (تلك بيانات Country/City غير مضمونة الدقة حاليًا).
Snapshot، بلا فلترة زمنية.

--- request_to_offer_rate (في هذا التقرير) ---
SSOT: نفس request_to_accepted_offer_rate/request_to_offer_rate في Executive
Dashboard حرفيًا — مؤشر Matching عام يُعاد استخدامه هنا كسياق، لا حساب مستقل.

--- top_growing_parts (Trending Parts) ---
Formula: مقارنة فترتين متساويتَي الطول (window_days الحالية مقابل السابقة
مباشرة) لعدد pur.purchase_requests لكل catalog_part_ref_id؛ growth = current - previous.
SSOT: pur.purchase_requests.created_at مباشرة — لا يحتاج ana.events (البيانات
متوفرة فعليًا في Domain مغلق أصلًا، لا داعٍ لتكرارها في ana). لا Backfill:
الفترة السابقة قد تكون فارغة فعليًا إن كانت قديمة قبل بداية النشاط الفعلي —
هذا واقع البيانات، لا خطأ حساب.
Null handling (Pre-Gate Corrective #5): window_days افتراضي 30 يُستخدَم فقط
عند غياب المعامل تمامًا من الطلب. قيمة رقمية خارج المدى [1, 365] (0، سالبة،
>365) تُرفَض صراحة بـ400 INVALID_WINDOW — لا تتحول بصمت للافتراضي. قيمة غير
رقمية (نص) تُرفَض تلقائيًا بـ422 (FastAPI/Pydantic Type Coercion المدمَج).
===========================================================================
كل المؤشرات أعلاه: Sensitivity = Internal (لا بيانات شخصية حساسة، أرقام
مجمَّعة فقط — لا IP/Login/Messaging هنا؛ تلك في نطاق AUD منفصل تمامًا).
لا Revenue/Profit/GMV/Commission/Refunds — V1 ليس طرفًا ماليًا (قرار حاكم صريح).
===========================================================================

===========================================================================
امتداد — User Analytics (أول جزء من Detailed Analytics)
===========================================================================

--- users_by_role / users_by_account_type / verified_sellers_count ---
Formula: GROUP BY primary_role | GROUP BY account_type | COUNT WHERE is_verified_seller=true
SSOT: iam.users مباشرة. Snapshot لحظي، بلا فلترة زمنية.
Sensitivity: Internal.

--- registrations_by_day ---
Formula: GROUP BY date_trunc('day', created_at) ضمن [date_from, date_to]
Null handling: يتطلب date_from و/أو date_to فعليًا؛ قائمة فارغة بلا مدى زمني
               (بنفس مبدأ users_new في Executive Dashboard — لا معنى لـ"الاتجاه
               اليومي" بلا نافذة زمنية محدَّدة).
لا Country/City/Language هنا: iam.users لا يحمل هذه الأعمدة في أي Migration
حالية (تحقُّق مصدري مباشر) — الاختراع ممنوع صراحة. البيانات الجغرافية
متاحة فقط على مستوى المتجر (str.stores)، تُعرَض ضمن Seller/Store Analytics.
===========================================================================

===========================================================================
امتداد — Consolidated Detailed Analytics (Seller/Store + Inventory/Catalog + PR/Offer)
===========================================================================

--- SellerStoreAnalytics ---
stores_by_status: GROUP BY status FROM str.stores — Snapshot
sellers_without_store_count: iam.users بدور بائع بلا صفّ مطابق في str.stores — Snapshot
active_stores_without_inventory_count: متجر نشط بلا أي str.inventory_items — Snapshot
top_stores_by_offer_count: GROUP BY seller_store_ref_id FROM pur.offers، أعلى 20 — Snapshot
new_stores_count: يتطلب date_from/date_to فعليًا (created_at ضمن المدى، Inclusive
                   Calendar Dates بنفس معيار _normalize_date_range)، وإلا 0

--- InventoryCatalogAnalytics ---
inventory_items_by_status / by_pricing_mode / catalog_parts_by_status /
manufacturers_by_status / models_total / generations_total / trims_total:
Snapshot مباشر من الجداول المعنية. لا عمود status في vct.generations/vct.trims
(عدّ إجمالي فقط، بلا تفصيل حالة).
stale_active_inventory_items_count: status='active' وupdated_at أقدم من 30 يومًا —
Snapshot. لا "عناصر بلا صور": primary_photo_id عمود يتيم فعليًا (بلا بنية تحتية
صور مخزون بعد) — أي مؤشر كهذا سيُظهِر 100% من العناصر "بلا صورة" دائمًا، مضلِّل
تمامًا وليس إشارة سلوك بائع حقيقية؛ الاختراع ممنوع صراحة.

--- PurchaseRequestOfferAnalytics ---
offers_by_status / withdrawn_offers_count: Snapshot مباشر من pur.offers
avg_hours_to_first_offer: متوسط الفارق (أول عرض.created_at - الطلب.created_at)
                           بالساعات، لكل الطلبات التي استقبلت عرضًا واحدًا فأكثر
avg_hours_to_accepted_offer: متوسط الفارق (العرض المقبول.updated_at - الطلب.created_at)
                              بالساعات، لكل الطلبات التي قُبِل فيها عرض
Null handling: None (لا صفر مضلِّل) إذا لا بيانات كافية لحساب المتوسط — مؤشر
               غير موجود أفضل من مؤشر خاطئ يوحي بـ"استجابة فورية".
Sensitivity: كل ما سبق Internal، بلا بيانات شخصية.
===========================================================================
"""

from datetime import datetime
from typing import Optional

from rpt_repository import (
    ExecutiveDashboard,
    InventoryCatalogAnalytics,
    MarketplaceIntelligence,
    MissingPartsReport,
    PurchaseRequestOfferAnalytics,
    SearchAnalytics,
    SellerStoreAnalytics,
    TrendingParts,
    UserAnalytics,
)


class InvalidDateRangeError(ValueError):
    pass


class InvalidWindowError(ValueError):
    pass


def _normalize_date_range(
    date_from: Optional[datetime], date_to: Optional[datetime],
) -> "tuple[Optional[datetime], Optional[datetime]]":
    """Root-Cause Fix (مراجعة ما قبل الرفع): date_from/date_to كلاهما "يوم تقويمي
    Inclusive بالكامل" بالنسبة للمستخدم — نقطة توحيد واحدة تخدم كل Endpoints
    هذا الملف (Executive Dashboard/Search Analytics/Missing Parts/Marketplace
    Intelligence/User Analytics؛ Marketplace Intelligence يستدعي Repository
    الأخرى داخليًا بنفس القيم المُطبَّعة هنا، فتُغطَّى تلقائيًا بلا تعديل مستقل).

    السبب الجذري المؤكَّد فعليًا (لا افتراض): <input type="date"> في الواجهة
    يُرسِل تاريخًا بلا وقت (مثل '2026-08-20')؛ FastAPI/Pydantic يُفسِّره
    كمنتصف الليل 00:00:00 تلقائيًا. كل استعلامات Repository الفعلية
    (rpt_repository.py، تحقَّقت من كل الأسطر) تستخدم `created_at <= date_to`/
    `occurred_at_utc <= date_to` حرفيًا بلا أي تعديل لنهاية اليوم — يستبعد هذا
    فعليًا كل سجلات يوم date_to تقريبًا (كل ما بعد 00:00:00.000000 بالضبط).

    الحل: تطبيع date_from لبداية يومه (00:00:00.000000) وdate_to لنهاية يومه
    (23:59:59.999999) هنا دائمًا، بصرف النظر عن أي مكوّن وقت وارد فعليًا في
    القيمة الأصلية — دلالة موحَّدة ومتوقَّعة لكل مستهلكي هذا الـEndpoint (لا
    تمييز بين "المستخدم قصد نصف الليل فعلًا" و"Default من Date Picker"،
    لأن العقد المُعلَن هو Calendar Date أصلًا، لا Timestamp دقيق).
    التحقق من date_from <= date_to يبقى على القيم الأصلية (مقارنة يوم بيوم
    فعليًا، لأن كليهما بلا وقت غالبًا) قبل التطبيع، لرسالة خطأ واضحة."""
    if date_from is not None and date_to is not None and date_from.date() > date_to.date():
        raise InvalidDateRangeError("date_from يجب ألا يكون بعد date_to.")
    normalized_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0) if date_from is not None else None
    normalized_to = date_to.replace(hour=23, minute=59, second=59, microsecond=999999) if date_to is not None else None
    return normalized_from, normalized_to


def get_executive_dashboard_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> ExecutiveDashboard:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_executive_dashboard(date_from, date_to)


def get_search_analytics_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> SearchAnalytics:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_search_analytics(date_from, date_to)


def get_missing_parts_report_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> MissingPartsReport:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_missing_parts_report(date_from, date_to)


def get_marketplace_intelligence_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> MarketplaceIntelligence:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_marketplace_intelligence(date_from, date_to)


def get_trending_parts_via_repository(repository, window_days: int = 30) -> TrendingParts:
    if window_days < 1 or window_days > 365:
        raise InvalidWindowError("window_days يجب أن يكون بين 1 و365.")
    return repository.get_trending_parts(window_days)


def get_user_analytics_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> UserAnalytics:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_user_analytics(date_from, date_to)


def get_seller_store_analytics_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> SellerStoreAnalytics:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_seller_store_analytics(date_from, date_to)


def get_inventory_catalog_analytics_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> InventoryCatalogAnalytics:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_inventory_catalog_analytics(date_from, date_to)


def get_purchase_request_offer_analytics_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> PurchaseRequestOfferAnalytics:
    date_from, date_to = _normalize_date_range(date_from, date_to)
    return repository.get_purchase_request_offer_analytics(date_from, date_to)
