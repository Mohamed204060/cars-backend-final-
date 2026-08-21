"""
rpt_repository.py — طبقة القراءة لتقارير الإدارة (Batch 3A Slice 2)
المرجع: CarsMaint Reporting, Analytics, Intelligence & Regulatory Reporting
        Catalog v1.0 §8 (Executive Dashboard) + §7 (Marketplace Matching & Conversion)

هذا الملف Read-Only بالكامل — لا INSERT/UPDATE/DELETE على أي جدول. يقرأ
مباشرة من جداول Domains أخرى مغلقة (iam.users، str.stores، str.inventory_items،
pct.catalog_parts، pur.purchase_requests، pur.offers، sub.seller_subscriptions)
دون أي تعديل عليها — نمط تقارير قياسي (Cross-Schema Aggregation)، لا يستدعي
أي Repository/Service من تلك الـDomains (تفادي اقتران غير ضروري، ولأن حِزَم
تلك الـDomains لا تعرض أصلًا دوال تجميع/إحصاء جاهزة).

Reporting Data Dictionary (SSOT مختصر لكل Metric هنا؛ التفصيل الكامل في
rpt_service.py):
- users_total/users_new: من iam.users، بلا فلترة دور.
- sellers_total: iam.users حيث primary_role IN ('individual_seller','business_seller').
- stores_total/by status: str.stores.status (creating/active/suspended/archived).
- inventory_items_*: str.inventory_items.status (active/out_of_stock/hidden/archived).
- catalog_parts_*: pct.catalog_parts.status (proposed/approved/archived).
- purchase_requests_*: pur.purchase_requests.status (open/under_review/fulfilled/expired/cancelled).
- offers_*: pur.offers.status (submitted/accepted/rejected/withdrawn/expired).
- requests_without_offers: طلبات بلا أي عرض على الإطلاق (بغضّ النظر عن حالته).
- request_to_offer_rate: طلبات استقبلت عرضًا واحدًا فأكثر ÷ إجمالي الطلبات.
- request_to_accepted_offer_rate: طلبات status='fulfilled' ÷ إجمالي الطلبات.
- avg_offers_per_request: إجمالي العروض ÷ إجمالي الطلبات (0 عند عدم وجود طلبات).
- subscriptions_by_plan: sub.seller_subscriptions (status='active') مجمَّعة حسب ref.ref_values.code.

Date Semantics: date_from/date_to (إن وُجدا) يُطبَّقان فقط على "New" (إنشاء
ضمن المدى) — الإجماليات/الحالات الحالية دائمًا لحظة الاستعلام (Snapshot)، لا
تُفلتَر بالمدى الزمني، تجنبًا لالتباس "عدد الحالة الآن" مقابل "عدد الحالة
خلال فترة" (لا Historical Backfill، تمامًا كما يوجب القرار الحاكم).

===========================================================================
امتداد Slice 2 — Search Analytics + Missing Parts/Unmet Demand
===========================================================================
المصدر: ana.events (Analytics Event Foundation، Slice 1) بعد ربط
search_api.py فعليًا الآن (Slice 2) بحدثَي search_performed/search_zero_results
فقط (ضمن الثمانية المعتمَدة أصلًا في §32 — بلا حدث جديد). لا بيانات قبل
تاريخ instrumentation الفعلي — القياس يبدأ من هناك حصرًا، بلا Backfill.

- search_volume: COUNT(ana.events WHERE event_type='search_performed').
- zero_result_count: COUNT(ana.events WHERE event_type='search_zero_results').
- zero_result_rate: zero_result_count / search_volume (0.0 إذا search_volume=0).
- top_zero_result_vehicles: GROUP BY metadata->>'trim_ref_id' لأحداث search_zero_results
  (فقط trim_ref_id — لا نص بحث خام مخزَّن أصلًا، قرار Data Minimization من
  Slice 2). لا اسم مركبة محلول هنا (لا Join مع vct — يبقى Foundation خفيفًا؛
  الحل يحدث في طبقة العرض/Frontend عبر vct API القائم عند الحاجة).
- purchase_requests_without_offers_by_part: GROUP BY catalog_part_ref_id
  لطلبات بلا أي عرض (نفس NOT EXISTS المستخدَم في purchase_requests_without_offers
  أعلاه، بإضافة تجميع). High-Demand/No-Supply الأوضح المتاح فعليًا من البيانات
  الحالية (بلا ادّعاء بيانات مخزون/تغطية غير موجودة).
===========================================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class ExecutiveDashboard:
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]

    users_total: int
    users_new: int
    users_by_status: dict  # {status: count}
    sellers_total: int

    stores_total: int
    stores_by_status: dict

    inventory_items_total: int
    inventory_items_by_status: dict

    catalog_parts_total: int
    catalog_parts_by_status: dict

    purchase_requests_total: int
    purchase_requests_by_status: dict
    purchase_requests_without_offers: int

    offers_total: int
    offers_by_status: dict

    request_to_offer_rate: float
    request_to_accepted_offer_rate: float
    avg_offers_per_request: float

    subscriptions_active_total: int
    subscriptions_by_plan: dict  # {plan_code: count}


@dataclass
class SearchAnalytics:
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    search_volume: int
    zero_result_count: int
    zero_result_rate: float
    top_zero_result_vehicles: list  # [{"trim_ref_id": str|None, "count": int}], مرتَّبة تنازليًا
    top_missing_search_terms: list  # [{"normalized_query_term": str, "count": int}] — Pre-Gate Corrective #3


@dataclass
class MissingPartsReport:
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    zero_result_search_count: int
    purchase_requests_without_offers_count: int
    top_unmet_demand_parts: list  # [{"catalog_part_ref_id": str, "requests_without_offers": int}] — Snapshot
    top_missing_search_terms: list  # [{"normalized_query_term": str, "count": int}] — يُفلتَر بالمدى الزمني، Pre-Gate Corrective #3


@dataclass
class MarketplaceIntelligence:
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    demand_hotspot_vehicles: list       # نفس top_zero_result_vehicles (إعادة استخدام، لا صيغة ثانية)
    unmet_demand_parts: list            # نفس top_unmet_demand_parts
    catalog_parts_with_no_active_supply: int  # قطع approved بلا أي عنصر مخزون status='active' فعليًا (Pre-Gate Corrective #2)
    sellers_to_active_stores_ratio: float     # sellers_total/active_stores_count (Pre-Gate Corrective #1)
    request_to_offer_rate: float        # إعادة استخدام من Executive Dashboard، لا حساب مزدوج


@dataclass
class TrendingParts:
    generated_at_utc: datetime
    window_days: int
    current_period_from: datetime
    current_period_to: datetime
    previous_period_from: datetime
    previous_period_to: datetime
    top_growing_parts: list  # [{"catalog_part_ref_id": str, "current_count": int, "previous_count": int, "growth": int}]


@dataclass
class UserAnalytics:
    """أول جزء من Detailed Analytics (User/Seller/Store/Inventory/Catalog/PR/Offers)
    بعد Executive Dashboard. لا Country/City/Language هنا عمدًا — iam.users لا
    يحمل هذه الأعمدة إطلاقًا في أي Migration حالية (تحقَّقت صراحة)؛ اختراعها
    غير مسموح. تلك البيانات متاحة على مستوى المتجر فقط (str.stores.country_ref_id/
    city_ref_id)، تُعرَض لاحقًا ضمن Seller/Store Analytics عند تنفيذه."""
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    registrations_by_day: list  # [{"date": "YYYY-MM-DD", "count": int}] — يتطلب date_from/date_to فعليًا
    users_by_role: dict         # {primary_role: count} — Snapshot
    users_by_account_type: dict  # {"individual"|"business": count} — Snapshot
    verified_sellers_count: int  # iam.users.is_verified_seller=true — Snapshot


@dataclass
class SellerStoreAnalytics:
    """Consolidated Detailed Analytics — الجزء الثاني (Seller/Store)."""
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    stores_by_status: dict            # Snapshot
    sellers_without_store_count: int  # Snapshot — بائع مسجَّل بلا متجر (فجوة تفعيل)
    active_stores_without_inventory_count: int  # Snapshot — متجر نشط بلا أي عنصر مخزون إطلاقًا
    top_stores_by_offer_count: list   # [{"store_id": str, "offer_count": int}] أعلى 20 — Snapshot
    new_stores_count: int             # يتطلب date_from/date_to فعليًا (created_at ضمن المدى)، وإلا 0


@dataclass
class InventoryCatalogAnalytics:
    """Consolidated Detailed Analytics — الجزء الثالث (Inventory/Catalog).
    لا 'عناصر بلا صور' هنا عمدًا: primary_photo_id عمود يتيم فعليًا (GAP-B
    موثَّقة مسبقًا — لا بنية تحتية لصور المخزون بعد)، فكل العناصر ستظهر
    'بلا صورة' بلا استثناء — مؤشر مضلِّل تمامًا، ليس إشارة سلوك بائع حقيقية."""
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    inventory_items_by_status: dict          # Snapshot
    inventory_items_by_pricing_mode: dict    # {"fixed_price"|"contact_for_price": count} — Snapshot
    stale_active_inventory_items_count: int  # status='active' وupdated_at أقدم من 30 يومًا — Snapshot
    catalog_parts_by_status: dict            # Snapshot
    manufacturers_by_status: dict            # Snapshot
    models_total: int                        # Snapshot (بلا تفصيل حالة — غير حاسم لهذا المستوى)
    generations_total: int                   # Snapshot (لا عمود status في vct.generations أصلًا)
    trims_total: int                         # Snapshot (لا عمود status في vct.trims أصلًا)


@dataclass
class PurchaseRequestOfferAnalytics:
    """Consolidated Detailed Analytics — الجزء الرابع (Purchase Request/Offer)."""
    generated_at_utc: datetime
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    offers_by_status: dict                        # Snapshot
    withdrawn_offers_count: int                    # Snapshot
    avg_hours_to_first_offer: Optional[float]      # Snapshot — None إن لا بيانات (لا صفر مضلِّل)
    avg_hours_to_accepted_offer: Optional[float]   # Snapshot — None إن لا بيانات


@dataclass
class Member360:
    """Member 360° (Reports Catalog §6) — ملف تحليلي شامل لمستخدم واحد.
    كل حقل هنا SSOT محدَّد صراحة أدناه؛ لا حقل مُخترَع. حقول غائبة فعليًا من
    النموذج الحالي (IP history، Failed login attempts، Admin actions
    المستهدِفة لهذا الحساب تحديدًا) غير مُضمَّنة هنا إطلاقًا — تُرفَع كـBlocker
    منفصل، لا تُقارَب بقيمة تقريبية أو استدلال غير موثوق.

    SSOT لكل حقل:
    - account/status/created_at/is_verified_seller: iam.users
    - store_ids: str.stores.owner_user_ref_id = user_id (بائع فقط)
    - subscription: sub.seller_subscriptions.seller_ref_id = user_id (بائع فقط،
      أحدث سجل)؛ None لغير البائع أو بائع بلا سجل (Free الضمنية، CR-014 —
      لا سجل DB لها أصلًا، فهذا ليس نقصًا بل تصميمًا معتمدًا)
    - inventory_items_*: str.inventory_items عبر store_ids (بائع فقط)
    - purchase_requests_*: pur.purchase_requests.buyer_user_ref_id = user_id
    - offers_*: pur.offers.seller_store_ref_id IN store_ids (بائع فقط)
    - conversations_count: عدد صفوف مميّزة في com.conversation_participants
      لهذا المستخدم (Metadata فقط — لا محتوى رسائل، §24)
    - support_tickets_*: sup.tickets.requester_ref_id = user_id
    - login_sessions_total/last_login_at/last_logout_at: iam.sessions
      (created_at = وقت الدخول الفعلي؛ لا عمود IP في هذا الجدول إطلاقًا —
      Blocker منفصل، ليس تقريبًا)
    - audit_events_as_actor_total: aud.events.actor_ref_id = user_id فقط
      (أفعال قام بها هذا المستخدم؛ aud.events لا يملك عمود Target/Subject،
      فلا يمكن استعلام "إجراءات إدارية اتُّخذت على هذا الحساب" بثقة — Blocker
      منفصل، لا استدلال من metadata/before_value/after_value غير موثَّق)
    """
    generated_at_utc: datetime
    user_id: str
    business_code: str
    primary_role: str
    account_type: str
    status: str
    created_at: datetime
    is_verified_seller: bool

    store_ids: list

    subscription_plan_code: Optional[str]
    subscription_status: Optional[str]
    subscription_expires_at: Optional[datetime]

    inventory_items_total: int
    inventory_items_by_status: dict

    purchase_requests_total: int
    purchase_requests_by_status: dict

    offers_total: int
    offers_by_status: dict

    conversations_count: int

    support_tickets_total: int
    support_tickets_by_status: dict

    login_sessions_total: int
    last_login_at: Optional[datetime]
    last_logout_at: Optional[datetime]

    audit_events_as_actor_total: int


@dataclass
class Store360:
    """Store 360° (Reports Catalog §8) — تقرير تفصيلي لمتجر واحد.

    SSOT لكل حقل:
    - store/status/created_at/owner_user_ref_id: str.stores
    - subscription: sub.seller_subscriptions.seller_ref_id = owner_user_ref_id
      (الاشتراك تابع للبائع/المستخدم لا للمتجر نفسه — لا عمود اشتراك مباشر
      على str.stores، هذا النموذج الفعلي القائم، لا اختراع)
    - inventory_items_*: str.inventory_items.store_id = store_id
    - offers_*/accepted_offers_total: pur.offers.seller_store_ref_id = store_id
    - response metrics: من pur.offers/pur.purchase_requests المرتبطة بالمتجر
    - media_active_images_total: media.attachments حيث owner_type='inventory_item'
      و owner_ref_id ضمن عناصر مخزون هذا المتجر، status='active'
    - البلاغات المرتبطة بالمتجر: Blocker — sup.tickets ليس له أي عمود
      store_ref_id، فلا رابط تقني موثوق بين تذكرة دعم ومتجر محدد حاليًا
    - Audit events: Blocker لنفس سبب Member360 (لا Target column)، ولا
      "actor" منطقي على مستوى المتجر أصلًا (المتجر ليس فاعلًا في IAM)
    """
    generated_at_utc: datetime
    store_id: str
    owner_user_ref_id: str
    status: str
    created_at: datetime

    subscription_plan_code: Optional[str]
    subscription_status: Optional[str]
    subscription_expires_at: Optional[datetime]

    inventory_items_total: int
    inventory_items_by_status: dict

    offers_total: int
    offers_by_status: dict
    accepted_offers_total: int
    accepted_offer_rate: float

    avg_hours_to_offer_response: Optional[float]

    media_active_images_total: int


@dataclass
class DataQualityDashboard:
    """Data Quality Dashboard (Reports Catalog §25) — وفق نموذج البيانات
    الفعلي فقط. Price Upon Contact حالة صحيحة معتمَدة (pricing_mode='contact')
    وليست خطأ (نفس المبدأ المذكور صراحة في §25 و§9).

    SSOT لكل حقل:
    - inventory_items_without_price: str.inventory_items حيث pricing_mode
      يستلزم سعرًا (fixed) والسعر NULL — استثناء صريح لـpricing_mode='contact'
    - inventory_items_without_images: str.inventory_items التي لا يوجد لها
      أي media.attachments (owner_type='inventory_item', status='active')
    - catalog_parts_incomplete: pct.catalog_parts بحالة 'proposed' (بانتظار
      اعتماد/إثراء) — لا حقل "اكتمال" صريح آخر في هذا النموذج
    - catalog_parts_not_linked_to_vehicle: pct.catalog_parts بلا أي صف في
      cmp.compatibility_records
    - vehicle_records_incomplete: Blocker — لا عمود/معيار "اكتمال" معتمَد
      صراحةً على vct.trims في النموذج الحالي (لا نخترع معيارًا)
    - excel_import_errors_recent: Blocker — لا جدول Excel Import سجلّات
      موجود فعليًا بعد في هذا المستودع (غير منفَّذ بعد، §27 Not Started)
    """
    generated_at_utc: datetime

    inventory_items_without_price: int
    inventory_items_without_images: int
    inventory_items_total: int

    catalog_parts_proposed_pending_review: int
    catalog_parts_not_linked_to_vehicle: int
    catalog_parts_total: int


class RptRepository(ABC):
    """Read-Only بالكامل — لا أي abstractmethod للكتابة، عمدًا، لأن هذا Domain
    لا يملك بيانات، يقرأ فقط من Domains أخرى."""

    @abstractmethod
    def get_executive_dashboard(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> ExecutiveDashboard:
        raise NotImplementedError

    @abstractmethod
    def get_search_analytics(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> SearchAnalytics:
        raise NotImplementedError

    @abstractmethod
    def get_missing_parts_report(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> MissingPartsReport:
        raise NotImplementedError

    @abstractmethod
    def get_marketplace_intelligence(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> MarketplaceIntelligence:
        raise NotImplementedError

    @abstractmethod
    def get_trending_parts(self, window_days: int) -> TrendingParts:
        raise NotImplementedError

    @abstractmethod
    def get_user_analytics(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> UserAnalytics:
        raise NotImplementedError

    @abstractmethod
    def get_seller_store_analytics(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> SellerStoreAnalytics:
        raise NotImplementedError

    @abstractmethod
    def get_inventory_catalog_analytics(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> InventoryCatalogAnalytics:
        raise NotImplementedError

    @abstractmethod
    def get_purchase_request_offer_analytics(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> PurchaseRequestOfferAnalytics:
        raise NotImplementedError

    @abstractmethod
    def get_member_360(self, user_id: str) -> Optional[Member360]:
        """None إن لم يوجد مستخدم بهذا الـid — التحقق من 404 مسؤولية cnt_api/الاستدعاء."""
        raise NotImplementedError

    @abstractmethod
    def get_store_360(self, store_id: str) -> Optional[Store360]:
        raise NotImplementedError

    @abstractmethod
    def get_data_quality_dashboard(self) -> DataQualityDashboard:
        raise NotImplementedError


class PostgresRptRepository(RptRepository):
    def __init__(self, connection):
        self._connection = connection

    @property
    def connection(self):
        return self._connection

    def get_executive_dashboard(self, date_from, date_to):
        with self._connection.cursor() as cur:
            # === Users ===
            cur.execute("SELECT COUNT(*) AS c FROM iam.users")
            users_total = cur.fetchone()["c"]

            if date_from is not None or date_to is not None:
                filters, params = [], {}
                if date_from is not None:
                    filters.append("created_at >= %(date_from)s")
                    params["date_from"] = date_from
                if date_to is not None:
                    filters.append("created_at <= %(date_to)s")
                    params["date_to"] = date_to
                cur.execute(f"SELECT COUNT(*) AS c FROM iam.users WHERE {' AND '.join(filters)}", params)
                users_new = cur.fetchone()["c"]
            else:
                users_new = 0  # بلا مدى زمني، "الجدد" غير معرَّف — Snapshot فقط (لا Historical Backfill)

            cur.execute("SELECT status, COUNT(*) AS c FROM iam.users GROUP BY status")
            users_by_status = {r["status"]: r["c"] for r in cur.fetchall()}

            cur.execute(
                "SELECT COUNT(*) AS c FROM iam.users WHERE primary_role IN ('individual_seller', 'business_seller')"
            )
            sellers_total = cur.fetchone()["c"]

            # === Stores ===
            cur.execute("SELECT status, COUNT(*) AS c FROM str.stores GROUP BY status")
            stores_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            stores_total = sum(stores_by_status.values())

            # === Inventory Items ===
            cur.execute("SELECT status, COUNT(*) AS c FROM str.inventory_items GROUP BY status")
            inventory_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            inventory_total = sum(inventory_by_status.values())

            # === Catalog Parts ===
            cur.execute("SELECT status, COUNT(*) AS c FROM pct.catalog_parts GROUP BY status")
            parts_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            parts_total = sum(parts_by_status.values())

            # === Purchase Requests ===
            cur.execute("SELECT status, COUNT(*) AS c FROM pur.purchase_requests GROUP BY status")
            pr_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            pr_total = sum(pr_by_status.values())

            cur.execute("""
                SELECT COUNT(*) AS c FROM pur.purchase_requests pr
                WHERE NOT EXISTS (SELECT 1 FROM pur.offers o WHERE o.purchase_request_id = pr.id)
            """)
            pr_without_offers = cur.fetchone()["c"]

            # === Offers ===
            cur.execute("SELECT status, COUNT(*) AS c FROM pur.offers GROUP BY status")
            offers_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            offers_total = sum(offers_by_status.values())

            # === Marketplace Matching & Conversion (§7) ===
            requests_with_offer = pr_total - pr_without_offers
            request_to_offer_rate = (requests_with_offer / pr_total) if pr_total > 0 else 0.0
            request_to_accepted_offer_rate = (pr_by_status.get("fulfilled", 0) / pr_total) if pr_total > 0 else 0.0
            avg_offers_per_request = (offers_total / pr_total) if pr_total > 0 else 0.0

            # === Subscriptions ===
            cur.execute("""
                SELECT rv.code, COUNT(*) AS c
                FROM sub.seller_subscriptions ss
                JOIN sub.plans p ON ss.plan_id = p.id
                JOIN ref.ref_values rv ON p.plan_type_ref_id = rv.id
                WHERE ss.status = 'active'
                GROUP BY rv.code
            """)
            sub_by_plan = {r["code"]: r["c"] for r in cur.fetchall()}
            sub_active_total = sum(sub_by_plan.values())

        return ExecutiveDashboard(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            users_total=users_total, users_new=users_new, users_by_status=users_by_status,
            sellers_total=sellers_total,
            stores_total=stores_total, stores_by_status=stores_by_status,
            inventory_items_total=inventory_total, inventory_items_by_status=inventory_by_status,
            catalog_parts_total=parts_total, catalog_parts_by_status=parts_by_status,
            purchase_requests_total=pr_total, purchase_requests_by_status=pr_by_status,
            purchase_requests_without_offers=pr_without_offers,
            offers_total=offers_total, offers_by_status=offers_by_status,
            request_to_offer_rate=request_to_offer_rate,
            request_to_accepted_offer_rate=request_to_accepted_offer_rate,
            avg_offers_per_request=avg_offers_per_request,
            subscriptions_active_total=sub_active_total, subscriptions_by_plan=sub_by_plan,
        )

    def get_search_analytics(self, date_from, date_to):
        filters, params = ["event_type = %(event_type)s"], {}
        if date_from is not None:
            filters.append("occurred_at_utc >= %(date_from)s")
            params["date_from"] = date_from
        if date_to is not None:
            filters.append("occurred_at_utc <= %(date_to)s")
            params["date_to"] = date_to

        with self._connection.cursor() as cur:
            p1 = dict(params, event_type="search_performed")
            cur.execute(f"SELECT COUNT(*) AS c FROM ana.events WHERE {' AND '.join(filters)}", p1)
            search_volume = cur.fetchone()["c"]

            p2 = dict(params, event_type="search_zero_results")
            cur.execute(f"SELECT COUNT(*) AS c FROM ana.events WHERE {' AND '.join(filters)}", p2)
            zero_result_count = cur.fetchone()["c"]

            cur.execute(
                f"SELECT metadata->>'trim_ref_id' AS trim_ref_id, COUNT(*) AS c "
                f"FROM ana.events WHERE {' AND '.join(filters)} "
                f"GROUP BY metadata->>'trim_ref_id' ORDER BY c DESC LIMIT 20",
                p2,
            )
            top_vehicles = [{"trim_ref_id": r["trim_ref_id"], "count": r["c"]} for r in cur.fetchall()]

            # Pre-Gate Corrective #3: مصطلحات البحث بلا نتائج — normalized_query_term
            # (نفس normalize_arabic_search_text المستخدَمة فعليًا في search_service.py
            # للمطابقة، لا نص خام) مسجَّلة الآن ضمن metadata (search_api.py). فقط
            # الأحداث التي فيها نص بحث فعلي (has_query_text=true).
            cur.execute(
                f"SELECT metadata->>'normalized_query_term' AS term, COUNT(*) AS c "
                f"FROM ana.events WHERE {' AND '.join(filters)} "
                f"AND metadata->>'normalized_query_term' IS NOT NULL "
                f"GROUP BY metadata->>'normalized_query_term' ORDER BY c DESC LIMIT 20",
                p2,
            )
            top_terms = [{"normalized_query_term": r["term"], "count": r["c"]} for r in cur.fetchall()]

        zero_result_rate = (zero_result_count / search_volume) if search_volume > 0 else 0.0
        return SearchAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            search_volume=search_volume, zero_result_count=zero_result_count,
            zero_result_rate=zero_result_rate, top_zero_result_vehicles=top_vehicles,
            top_missing_search_terms=top_terms,
        )

    def get_missing_parts_report(self, date_from, date_to):
        event_filters, event_params = ["event_type = 'search_zero_results'"], {}
        if date_from is not None:
            event_filters.append("occurred_at_utc >= %(date_from)s")
            event_params["date_from"] = date_from
        if date_to is not None:
            event_filters.append("occurred_at_utc <= %(date_to)s")
            event_params["date_to"] = date_to

        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM ana.events WHERE {' AND '.join(event_filters)}", event_params)
            zero_result_search_count = cur.fetchone()["c"]

            # Pre-Gate Corrective #3: أهم إضافة — مصطلحات البحث الفعلية بلا نتائج،
            # لا عدّ إجمالي فقط. مُفلترة بنفس المدى الزمني لأنها من ana.events.
            cur.execute(
                f"SELECT metadata->>'normalized_query_term' AS term, COUNT(*) AS c "
                f"FROM ana.events WHERE {' AND '.join(event_filters)} "
                f"AND metadata->>'normalized_query_term' IS NOT NULL "
                f"GROUP BY metadata->>'normalized_query_term' ORDER BY c DESC LIMIT 20",
                event_params,
            )
            top_terms = [{"normalized_query_term": r["term"], "count": r["c"]} for r in cur.fetchall()]

            # الطلبات نفسها (pur.purchase_requests) بلا فلترة زمنية هنا عمدًا:
            # "بلا عروض" حالة Snapshot لحظية، بنفس مبدأ purchase_requests_without_offers
            # في get_executive_dashboard أعلاه — لا فلترة بتاريخ الإنشاء. (Pre-Gate
            # Corrective #4: موثَّق صراحة الآن في rpt_service.py وOpenAPI وFrontend
            # — لا يجوز افتراض أن كل حقول هذا التقرير تخص المدى الزمني المُختار.)
            cur.execute("""
                SELECT COUNT(*) AS c FROM pur.purchase_requests pr
                WHERE NOT EXISTS (SELECT 1 FROM pur.offers o WHERE o.purchase_request_id = pr.id)
            """)
            pr_without_offers_total = cur.fetchone()["c"]

            cur.execute("""
                SELECT pr.catalog_part_ref_id, COUNT(*) AS c
                FROM pur.purchase_requests pr
                WHERE NOT EXISTS (SELECT 1 FROM pur.offers o WHERE o.purchase_request_id = pr.id)
                GROUP BY pr.catalog_part_ref_id
                ORDER BY c DESC LIMIT 20
            """)
            top_parts = [{"catalog_part_ref_id": r["catalog_part_ref_id"], "requests_without_offers": r["c"]}
                         for r in cur.fetchall()]

        return MissingPartsReport(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            zero_result_search_count=zero_result_search_count,
            purchase_requests_without_offers_count=pr_without_offers_total,
            top_unmet_demand_parts=top_parts,
            top_missing_search_terms=top_terms,
        )

    def get_marketplace_intelligence(self, date_from, date_to):
        # إعادة استخدام كاملة لصيغتَي Missing Parts وSearch Analytics — لا حساب مزدوج لنفس المؤشر
        search = self.get_search_analytics(date_from, date_to)
        missing = self.get_missing_parts_report(date_from, date_to)
        dashboard = self.get_executive_dashboard(date_from, date_to)

        with self._connection.cursor() as cur:
            # Pre-Gate Corrective #2: "Supply حقيقية قابلة للاستخدام الآن" تعني
            # status='active' حصرًا. out_of_stock استُبعِدت عمدًا هنا (خلافًا
            # لـsearch_repository.py الذي يعرضها للتصفح فقط) — لا شيء قابل
            # للشراء فعليًا الآن من عنصر out_of_stock، وهذا المؤشر يقيس فجوة
            # عرض حقيقية للإدارة، لا مجرد قابلية الظهور في نتائج البحث.
            cur.execute("""
                SELECT COUNT(*) AS c FROM pct.catalog_parts cp
                WHERE cp.status = 'approved'
                AND NOT EXISTS (
                    SELECT 1 FROM str.inventory_items ii
                    WHERE ii.catalog_part_ref_id = cp.id AND ii.status = 'active'
                )
            """)
            zero_active_supply_parts = cur.fetchone()["c"]

            # Pre-Gate Corrective #1: المقام active stores حصرًا — متجر مُعلَّق/مؤرشَف
            # لا يمثّل تغطية عرض فعلية، رغم وجود صفّه في الجدول.
            cur.execute("SELECT COUNT(*) AS c FROM str.stores WHERE status = 'active'")
            active_stores_count = cur.fetchone()["c"]

        sellers_to_active_stores_ratio = (
            (dashboard.sellers_total / active_stores_count) if active_stores_count > 0 else 0.0
        )

        return MarketplaceIntelligence(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            demand_hotspot_vehicles=search.top_zero_result_vehicles,
            unmet_demand_parts=missing.top_unmet_demand_parts,
            catalog_parts_with_no_active_supply=zero_active_supply_parts,
            sellers_to_active_stores_ratio=sellers_to_active_stores_ratio,
            request_to_offer_rate=dashboard.request_to_offer_rate,
        )

    def get_trending_parts(self, window_days: int):
        from datetime import timedelta
        now = datetime.utcnow()
        current_from, current_to = now - timedelta(days=window_days), now
        previous_from, previous_to = now - timedelta(days=window_days * 2), now - timedelta(days=window_days)

        with self._connection.cursor() as cur:
            cur.execute("""
                SELECT catalog_part_ref_id, COUNT(*) AS c FROM pur.purchase_requests
                WHERE created_at >= %(f)s AND created_at < %(t)s
                GROUP BY catalog_part_ref_id
            """, {"f": current_from, "t": current_to})
            current_counts = {r["catalog_part_ref_id"]: r["c"] for r in cur.fetchall()}

            cur.execute("""
                SELECT catalog_part_ref_id, COUNT(*) AS c FROM pur.purchase_requests
                WHERE created_at >= %(f)s AND created_at < %(t)s
                GROUP BY catalog_part_ref_id
            """, {"f": previous_from, "t": previous_to})
            previous_counts = {r["catalog_part_ref_id"]: r["c"] for r in cur.fetchall()}

        all_parts = set(current_counts) | set(previous_counts)
        rows = [
            {
                "catalog_part_ref_id": part_id,
                "current_count": current_counts.get(part_id, 0),
                "previous_count": previous_counts.get(part_id, 0),
                "growth": current_counts.get(part_id, 0) - previous_counts.get(part_id, 0),
            }
            for part_id in all_parts
        ]
        rows.sort(key=lambda r: r["growth"], reverse=True)

        return TrendingParts(
            generated_at_utc=now, window_days=window_days,
            current_period_from=current_from, current_period_to=current_to,
            previous_period_from=previous_from, previous_period_to=previous_to,
            top_growing_parts=rows[:20],
        )

    def get_user_analytics(self, date_from, date_to):
        with self._connection.cursor() as cur:
            cur.execute("SELECT primary_role, COUNT(*) AS c FROM iam.users GROUP BY primary_role")
            users_by_role = {r["primary_role"]: r["c"] for r in cur.fetchall()}

            cur.execute("SELECT account_type, COUNT(*) AS c FROM iam.users GROUP BY account_type")
            users_by_account_type = {r["account_type"]: r["c"] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(*) AS c FROM iam.users WHERE is_verified_seller = true")
            verified_sellers_count = cur.fetchone()["c"]

            registrations_by_day = []
            if date_from is not None or date_to is not None:
                filters, params = [], {}
                if date_from is not None:
                    filters.append("created_at >= %(date_from)s")
                    params["date_from"] = date_from
                if date_to is not None:
                    filters.append("created_at <= %(date_to)s")
                    params["date_to"] = date_to
                cur.execute(
                    f"SELECT date_trunc('day', created_at)::date AS day, COUNT(*) AS c "
                    f"FROM iam.users WHERE {' AND '.join(filters)} GROUP BY day ORDER BY day",
                    params,
                )
                registrations_by_day = [{"date": r["day"].isoformat(), "count": r["c"]} for r in cur.fetchall()]

        return UserAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            registrations_by_day=registrations_by_day, users_by_role=users_by_role,
            users_by_account_type=users_by_account_type, verified_sellers_count=verified_sellers_count,
        )

    def get_seller_store_analytics(self, date_from, date_to):
        with self._connection.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) AS c FROM str.stores GROUP BY status")
            stores_by_status = {r["status"]: r["c"] for r in cur.fetchall()}

            cur.execute("""
                SELECT COUNT(*) AS c FROM iam.users u
                WHERE u.primary_role IN ('individual_seller', 'business_seller')
                AND NOT EXISTS (SELECT 1 FROM str.stores s WHERE s.owner_user_ref_id = u.id)
            """)
            sellers_without_store_count = cur.fetchone()["c"]

            cur.execute("""
                SELECT COUNT(*) AS c FROM str.stores s
                WHERE s.status = 'active'
                AND NOT EXISTS (SELECT 1 FROM str.inventory_items ii WHERE ii.store_id = s.id)
            """)
            active_stores_without_inventory_count = cur.fetchone()["c"]

            cur.execute("""
                SELECT seller_store_ref_id, COUNT(*) AS c FROM pur.offers
                GROUP BY seller_store_ref_id ORDER BY c DESC LIMIT 20
            """)
            top_stores_by_offer_count = [{"store_id": r["seller_store_ref_id"], "offer_count": r["c"]} for r in cur.fetchall()]

            new_stores_count = 0
            if date_from is not None or date_to is not None:
                filters, params = [], {}
                if date_from is not None:
                    filters.append("created_at >= %(date_from)s")
                    params["date_from"] = date_from
                if date_to is not None:
                    filters.append("created_at <= %(date_to)s")
                    params["date_to"] = date_to
                cur.execute(f"SELECT COUNT(*) AS c FROM str.stores WHERE {' AND '.join(filters)}", params)
                new_stores_count = cur.fetchone()["c"]

        return SellerStoreAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            stores_by_status=stores_by_status, sellers_without_store_count=sellers_without_store_count,
            active_stores_without_inventory_count=active_stores_without_inventory_count,
            top_stores_by_offer_count=top_stores_by_offer_count, new_stores_count=new_stores_count,
        )

    def get_inventory_catalog_analytics(self, date_from, date_to):
        with self._connection.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) AS c FROM str.inventory_items GROUP BY status")
            inventory_items_by_status = {r["status"]: r["c"] for r in cur.fetchall()}

            cur.execute("SELECT pricing_mode, COUNT(*) AS c FROM str.inventory_items GROUP BY pricing_mode")
            inventory_items_by_pricing_mode = {r["pricing_mode"]: r["c"] for r in cur.fetchall()}

            cur.execute("""
                SELECT COUNT(*) AS c FROM str.inventory_items
                WHERE status = 'active' AND updated_at < now() - interval '30 days'
            """)
            stale_active_inventory_items_count = cur.fetchone()["c"]

            cur.execute("SELECT status, COUNT(*) AS c FROM pct.catalog_parts GROUP BY status")
            catalog_parts_by_status = {r["status"]: r["c"] for r in cur.fetchall()}

            cur.execute("SELECT status, COUNT(*) AS c FROM vct.manufacturers GROUP BY status")
            manufacturers_by_status = {r["status"]: r["c"] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(*) AS c FROM vct.models")
            models_total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM vct.generations")
            generations_total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM vct.trims")
            trims_total = cur.fetchone()["c"]

        return InventoryCatalogAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            inventory_items_by_status=inventory_items_by_status,
            inventory_items_by_pricing_mode=inventory_items_by_pricing_mode,
            stale_active_inventory_items_count=stale_active_inventory_items_count,
            catalog_parts_by_status=catalog_parts_by_status, manufacturers_by_status=manufacturers_by_status,
            models_total=models_total, generations_total=generations_total, trims_total=trims_total,
        )

    def get_purchase_request_offer_analytics(self, date_from, date_to):
        with self._connection.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) AS c FROM pur.offers GROUP BY status")
            offers_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            withdrawn_offers_count = offers_by_status.get("withdrawn", 0)

            cur.execute("""
                SELECT AVG(EXTRACT(EPOCH FROM (first_offer.min_created_at - pr.created_at)) / 3600.0) AS avg_hours
                FROM pur.purchase_requests pr
                JOIN (
                    SELECT purchase_request_id, MIN(created_at) AS min_created_at
                    FROM pur.offers GROUP BY purchase_request_id
                ) first_offer ON first_offer.purchase_request_id = pr.id
            """)
            row = cur.fetchone()
            avg_hours_to_first_offer = float(row["avg_hours"]) if row["avg_hours"] is not None else None

            cur.execute("""
                SELECT AVG(EXTRACT(EPOCH FROM (o.updated_at - pr.created_at)) / 3600.0) AS avg_hours
                FROM pur.purchase_requests pr
                JOIN pur.offers o ON o.purchase_request_id = pr.id AND o.status = 'accepted'
            """)
            row2 = cur.fetchone()
            avg_hours_to_accepted_offer = float(row2["avg_hours"]) if row2["avg_hours"] is not None else None

        return PurchaseRequestOfferAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            offers_by_status=offers_by_status, withdrawn_offers_count=withdrawn_offers_count,
            avg_hours_to_first_offer=avg_hours_to_first_offer,
            avg_hours_to_accepted_offer=avg_hours_to_accepted_offer,
        )

    def get_member_360(self, user_id):
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, business_code, primary_role, account_type, status, created_at, is_verified_seller "
                "FROM iam.users WHERE id = %(id)s",
                {"id": user_id},
            )
            user_row = cur.fetchone()
            if user_row is None:
                return None

            cur.execute("SELECT id FROM str.stores WHERE owner_user_ref_id = %(id)s", {"id": user_id})
            store_ids = [r["id"] for r in cur.fetchall()]

            cur.execute(
                "SELECT rv.code AS plan_code, ss.status, ss.expires_at "
                "FROM sub.seller_subscriptions ss "
                "JOIN sub.plans p ON p.id = ss.plan_id "
                "JOIN ref.ref_values rv ON rv.id = p.plan_type_ref_id "
                "WHERE ss.seller_ref_id = %(id)s ORDER BY ss.created_at DESC LIMIT 1",
                {"id": user_id},
            )
            sub_row = cur.fetchone()
            subscription_plan_code = sub_row["plan_code"] if sub_row else None
            subscription_status = sub_row["status"] if sub_row else None
            subscription_expires_at = sub_row["expires_at"] if sub_row else None

            inventory_items_by_status: dict = {}
            if store_ids:
                cur.execute(
                    "SELECT status, COUNT(*) AS c FROM str.inventory_items WHERE store_id = ANY(%(ids)s) GROUP BY status",
                    {"ids": store_ids},
                )
                inventory_items_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            inventory_items_total = sum(inventory_items_by_status.values())

            cur.execute(
                "SELECT status, COUNT(*) AS c FROM pur.purchase_requests WHERE buyer_user_ref_id = %(id)s GROUP BY status",
                {"id": user_id},
            )
            purchase_requests_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            purchase_requests_total = sum(purchase_requests_by_status.values())

            offers_by_status: dict = {}
            if store_ids:
                cur.execute(
                    "SELECT status, COUNT(*) AS c FROM pur.offers WHERE seller_store_ref_id = ANY(%(ids)s) GROUP BY status",
                    {"ids": store_ids},
                )
                offers_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            offers_total = sum(offers_by_status.values())

            cur.execute(
                "SELECT COUNT(DISTINCT conversation_id) AS c FROM com.conversation_participants WHERE user_ref_id = %(id)s",
                {"id": user_id},
            )
            conversations_count = cur.fetchone()["c"]

            cur.execute(
                "SELECT status, COUNT(*) AS c FROM sup.tickets WHERE requester_ref_id = %(id)s GROUP BY status",
                {"id": user_id},
            )
            support_tickets_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            support_tickets_total = sum(support_tickets_by_status.values())

            cur.execute(
                "SELECT COUNT(*) AS c, MAX(created_at) AS last_login, "
                "MAX(revoked_at) FILTER (WHERE revoked_reason = 'logout') AS last_logout "
                "FROM iam.sessions WHERE user_id = %(id)s",
                {"id": user_id},
            )
            session_row = cur.fetchone()
            login_sessions_total = session_row["c"]
            last_login_at = session_row["last_login"]
            last_logout_at = session_row["last_logout"]

            cur.execute("SELECT COUNT(*) AS c FROM aud.events WHERE actor_ref_id = %(id)s", {"id": user_id})
            audit_events_as_actor_total = cur.fetchone()["c"]

        return Member360(
            generated_at_utc=datetime.utcnow(), user_id=user_row["id"], business_code=user_row["business_code"],
            primary_role=user_row["primary_role"], account_type=user_row["account_type"],
            status=user_row["status"], created_at=user_row["created_at"],
            is_verified_seller=user_row["is_verified_seller"], store_ids=store_ids,
            subscription_plan_code=subscription_plan_code, subscription_status=subscription_status,
            subscription_expires_at=subscription_expires_at,
            inventory_items_total=inventory_items_total, inventory_items_by_status=inventory_items_by_status,
            purchase_requests_total=purchase_requests_total, purchase_requests_by_status=purchase_requests_by_status,
            offers_total=offers_total, offers_by_status=offers_by_status,
            conversations_count=conversations_count,
            support_tickets_total=support_tickets_total, support_tickets_by_status=support_tickets_by_status,
            login_sessions_total=login_sessions_total, last_login_at=last_login_at, last_logout_at=last_logout_at,
            audit_events_as_actor_total=audit_events_as_actor_total,
        )

    def get_store_360(self, store_id):
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, owner_user_ref_id, status, created_at FROM str.stores WHERE id = %(id)s",
                {"id": store_id},
            )
            store_row = cur.fetchone()
            if store_row is None:
                return None

            owner_id = store_row["owner_user_ref_id"]
            cur.execute(
                "SELECT rv.code AS plan_code, ss.status, ss.expires_at "
                "FROM sub.seller_subscriptions ss "
                "JOIN sub.plans p ON p.id = ss.plan_id "
                "JOIN ref.ref_values rv ON rv.id = p.plan_type_ref_id "
                "WHERE ss.seller_ref_id = %(id)s ORDER BY ss.created_at DESC LIMIT 1",
                {"id": owner_id},
            )
            sub_row = cur.fetchone()
            subscription_plan_code = sub_row["plan_code"] if sub_row else None
            subscription_status = sub_row["status"] if sub_row else None
            subscription_expires_at = sub_row["expires_at"] if sub_row else None

            cur.execute(
                "SELECT status, COUNT(*) AS c FROM str.inventory_items WHERE store_id = %(id)s GROUP BY status",
                {"id": store_id},
            )
            inventory_items_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            inventory_items_total = sum(inventory_items_by_status.values())

            cur.execute(
                "SELECT status, COUNT(*) AS c FROM pur.offers WHERE seller_store_ref_id = %(id)s GROUP BY status",
                {"id": store_id},
            )
            offers_by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            offers_total = sum(offers_by_status.values())
            accepted_offers_total = offers_by_status.get("accepted", 0)
            accepted_offer_rate = (accepted_offers_total / offers_total) if offers_total > 0 else 0.0

            cur.execute(
                "SELECT AVG(EXTRACT(EPOCH FROM (o.updated_at - pr.created_at)) / 3600.0) AS avg_hours "
                "FROM pur.offers o JOIN pur.purchase_requests pr ON pr.id = o.purchase_request_id "
                "WHERE o.seller_store_ref_id = %(id)s",
                {"id": store_id},
            )
            row = cur.fetchone()
            avg_hours_to_offer_response = float(row["avg_hours"]) if row["avg_hours"] is not None else None

            cur.execute(
                "SELECT COUNT(*) AS c FROM media.attachments ma "
                "JOIN str.inventory_items ii ON ii.id = ma.owner_ref_id "
                "WHERE ma.owner_type = 'inventory_item' AND ma.status = 'active' AND ii.store_id = %(id)s",
                {"id": store_id},
            )
            media_active_images_total = cur.fetchone()["c"]

        return Store360(
            generated_at_utc=datetime.utcnow(), store_id=store_row["id"], owner_user_ref_id=owner_id,
            status=store_row["status"], created_at=store_row["created_at"],
            subscription_plan_code=subscription_plan_code, subscription_status=subscription_status,
            subscription_expires_at=subscription_expires_at,
            inventory_items_total=inventory_items_total, inventory_items_by_status=inventory_items_by_status,
            offers_total=offers_total, offers_by_status=offers_by_status,
            accepted_offers_total=accepted_offers_total, accepted_offer_rate=accepted_offer_rate,
            avg_hours_to_offer_response=avg_hours_to_offer_response,
            media_active_images_total=media_active_images_total,
        )

    def get_data_quality_dashboard(self):
        with self._connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM str.inventory_items")
            inventory_items_total = cur.fetchone()["c"]

            # Price Upon Contact (pricing_mode='contact_for_price') مستثنى صراحةً
            # — حالة صحيحة معتمَدة، ليست نقص بيانات (§9، §25). ملاحظة: قيد
            # CHECK فعلي (chk_inventory_items_price_mode) يمنع أصلًا وجود
            # fixed_price بلا price_amount على مستوى القاعدة — هذا الاستعلام
            # يُثبِت ذلك بيانيًا (يُتوقَّع 0 دائمًا)، لا يخترع سيناريو غير ممكن.
            cur.execute(
                "SELECT COUNT(*) AS c FROM str.inventory_items "
                "WHERE pricing_mode = 'fixed_price' AND price_amount IS NULL"
            )
            inventory_items_without_price = cur.fetchone()["c"]

            cur.execute("""
                SELECT COUNT(*) AS c FROM str.inventory_items ii
                WHERE NOT EXISTS (
                    SELECT 1 FROM media.attachments ma
                    WHERE ma.owner_type = 'inventory_item' AND ma.owner_ref_id = ii.id AND ma.status = 'active'
                )
            """)
            inventory_items_without_images = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(*) AS c FROM pct.catalog_parts")
            catalog_parts_total = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(*) AS c FROM pct.catalog_parts WHERE status = 'proposed'")
            catalog_parts_proposed_pending_review = cur.fetchone()["c"]

            cur.execute("""
                SELECT COUNT(*) AS c FROM pct.catalog_parts cp
                WHERE NOT EXISTS (
                    SELECT 1 FROM cmp.compatibility_records cr WHERE cr.catalog_part_ref_id = cp.id
                )
            """)
            catalog_parts_not_linked_to_vehicle = cur.fetchone()["c"]

        return DataQualityDashboard(
            generated_at_utc=datetime.utcnow(),
            inventory_items_without_price=inventory_items_without_price,
            inventory_items_without_images=inventory_items_without_images,
            inventory_items_total=inventory_items_total,
            catalog_parts_proposed_pending_review=catalog_parts_proposed_pending_review,
            catalog_parts_not_linked_to_vehicle=catalog_parts_not_linked_to_vehicle,
            catalog_parts_total=catalog_parts_total,
        )


class InMemoryRptRepository(RptRepository):
    """للاختبارات فقط. لا يحاكي Repositories الأخرى — يقبل بيانات خام مُدخَلة
    مباشرة (قوائم قواميس تمثّل صفوف الجداول) لعزل اختبار منطق التجميع/الصيغ
    هنا عن أي اعتماد على Domains أخرى، تمامًا كما توصي به طريقة SSOT أعلاه."""

    def __init__(self):
        self.users: list[dict] = []           # {"status": str, "primary_role": str, "account_type": str (اختياري), "is_verified_seller": bool (اختياري), "created_at": datetime, "id": str (اختياري، لـSeller/Store فقط)}
        self.stores: list[dict] = []           # {"status": str, "id": str (اختياري), "owner_user_ref_id": str (اختياري), "created_at": datetime (اختياري)}
        self.inventory_items: list[dict] = []  # {"status": str, "catalog_part_ref_id": str (اختياري), "store_id": str (اختياري), "pricing_mode": str (اختياري), "updated_at": datetime (اختياري)}
        self.catalog_parts: list[dict] = []    # {"status": str, "id": str (اختياري، لـMarketplace Intelligence فقط)}
        self.purchase_requests: list[dict] = []  # {"id": str, "status": str, "catalog_part_ref_id": str, "created_at": datetime (اختياري)}
        self.offers: list[dict] = []           # {"purchase_request_id": str, "status": str, "seller_store_ref_id": str (اختياري), "created_at": datetime (اختياري)، "updated_at": datetime (اختياري)}
        self.subscriptions: list[dict] = []    # {"status": str, "plan_code": str}
        self.ana_events: list[dict] = []       # {"event_type": str, "occurred_at_utc": datetime, "metadata": dict}
        self.manufacturers: list[dict] = []    # {"status": str}
        self.models: list[dict] = []           # []، فقط للعدّ
        self.generations: list[dict] = []      # []، فقط للعدّ
        self.trims: list[dict] = []            # []، فقط للعدّ

        # Member 360 / Store 360 / Data Quality (Corrective batch)
        self.conversation_participants: list[dict] = []  # {"conversation_id": str, "user_ref_id": str}
        self.support_tickets: list[dict] = []             # {"requester_ref_id": str, "status": str}
        self.sessions: list[dict] = []                    # {"user_id": str, "created_at": datetime, "revoked_at": datetime|None, "revoked_reason": str|None}
        self.audit_events: list[dict] = []                # {"actor_ref_id": str}
        self.compatibility_records: list[dict] = []       # {"catalog_part_ref_id": str}
        self.media_attachments: list[dict] = []           # {"owner_type": str, "owner_ref_id": str, "status": str}

    def get_executive_dashboard(self, date_from, date_to):
        users_by_status: dict = {}
        for u in self.users:
            users_by_status[u["status"]] = users_by_status.get(u["status"], 0) + 1
        users_total = len(self.users)
        if date_from is not None or date_to is not None:
            users_new = sum(
                1 for u in self.users
                if (date_from is None or u["created_at"] >= date_from)
                and (date_to is None or u["created_at"] <= date_to)
            )
        else:
            users_new = 0
        sellers_total = sum(1 for u in self.users if u["primary_role"] in ("individual_seller", "business_seller"))

        stores_by_status: dict = {}
        for s in self.stores:
            stores_by_status[s["status"]] = stores_by_status.get(s["status"], 0) + 1

        inv_by_status: dict = {}
        for i in self.inventory_items:
            inv_by_status[i["status"]] = inv_by_status.get(i["status"], 0) + 1

        parts_by_status: dict = {}
        for p in self.catalog_parts:
            parts_by_status[p["status"]] = parts_by_status.get(p["status"], 0) + 1

        pr_by_status: dict = {}
        for pr in self.purchase_requests:
            pr_by_status[pr["status"]] = pr_by_status.get(pr["status"], 0) + 1
        pr_total = len(self.purchase_requests)

        prs_with_offer = {o["purchase_request_id"] for o in self.offers}
        pr_without_offers = sum(1 for pr in self.purchase_requests if pr["id"] not in prs_with_offer)

        offers_by_status: dict = {}
        for o in self.offers:
            offers_by_status[o["status"]] = offers_by_status.get(o["status"], 0) + 1
        offers_total = len(self.offers)

        requests_with_offer_count = pr_total - pr_without_offers
        request_to_offer_rate = (requests_with_offer_count / pr_total) if pr_total > 0 else 0.0
        request_to_accepted_offer_rate = (pr_by_status.get("fulfilled", 0) / pr_total) if pr_total > 0 else 0.0
        avg_offers_per_request = (offers_total / pr_total) if pr_total > 0 else 0.0

        sub_by_plan: dict = {}
        for s in self.subscriptions:
            if s["status"] == "active":
                sub_by_plan[s["plan_code"]] = sub_by_plan.get(s["plan_code"], 0) + 1

        return ExecutiveDashboard(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            users_total=users_total, users_new=users_new, users_by_status=users_by_status,
            sellers_total=sellers_total,
            stores_total=len(self.stores), stores_by_status=stores_by_status,
            inventory_items_total=len(self.inventory_items), inventory_items_by_status=inv_by_status,
            catalog_parts_total=len(self.catalog_parts), catalog_parts_by_status=parts_by_status,
            purchase_requests_total=pr_total, purchase_requests_by_status=pr_by_status,
            purchase_requests_without_offers=pr_without_offers,
            offers_total=offers_total, offers_by_status=offers_by_status,
            request_to_offer_rate=request_to_offer_rate,
            request_to_accepted_offer_rate=request_to_accepted_offer_rate,
            avg_offers_per_request=avg_offers_per_request,
            subscriptions_active_total=sum(sub_by_plan.values()), subscriptions_by_plan=sub_by_plan,
        )

    def _filter_events_by_date(self, event_type, date_from, date_to):
        return [
            e for e in self.ana_events
            if e["event_type"] == event_type
            and (date_from is None or e["occurred_at_utc"] >= date_from)
            and (date_to is None or e["occurred_at_utc"] <= date_to)
        ]

    def get_search_analytics(self, date_from, date_to):
        performed = self._filter_events_by_date("search_performed", date_from, date_to)
        zero = self._filter_events_by_date("search_zero_results", date_from, date_to)

        counts: dict = {}
        for e in zero:
            key = e.get("metadata", {}).get("trim_ref_id")
            counts[key] = counts.get(key, 0) + 1
        top_vehicles = sorted(
            ({"trim_ref_id": k, "count": v} for k, v in counts.items()), key=lambda x: x["count"], reverse=True
        )[:20]

        term_counts: dict = {}
        for e in zero:
            term = e.get("metadata", {}).get("normalized_query_term")
            if term is not None:
                term_counts[term] = term_counts.get(term, 0) + 1
        top_terms = sorted(
            ({"normalized_query_term": k, "count": v} for k, v in term_counts.items()),
            key=lambda x: x["count"], reverse=True,
        )[:20]

        search_volume = len(performed)
        zero_result_count = len(zero)
        zero_result_rate = (zero_result_count / search_volume) if search_volume > 0 else 0.0
        return SearchAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            search_volume=search_volume, zero_result_count=zero_result_count,
            zero_result_rate=zero_result_rate, top_zero_result_vehicles=top_vehicles,
            top_missing_search_terms=top_terms,
        )

    def get_missing_parts_report(self, date_from, date_to):
        zero = self._filter_events_by_date("search_zero_results", date_from, date_to)

        term_counts: dict = {}
        for e in zero:
            term = e.get("metadata", {}).get("normalized_query_term")
            if term is not None:
                term_counts[term] = term_counts.get(term, 0) + 1
        top_terms = sorted(
            ({"normalized_query_term": k, "count": v} for k, v in term_counts.items()),
            key=lambda x: x["count"], reverse=True,
        )[:20]

        prs_with_offer = {o["purchase_request_id"] for o in self.offers}
        without_offers = [pr for pr in self.purchase_requests if pr["id"] not in prs_with_offer]

        counts: dict = {}
        for pr in without_offers:
            key = pr.get("catalog_part_ref_id")
            counts[key] = counts.get(key, 0) + 1
        top_parts = sorted(
            ({"catalog_part_ref_id": k, "requests_without_offers": v} for k, v in counts.items()),
            key=lambda x: x["requests_without_offers"], reverse=True,
        )[:20]

        return MissingPartsReport(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            zero_result_search_count=len(zero),
            purchase_requests_without_offers_count=len(without_offers),
            top_unmet_demand_parts=top_parts,
            top_missing_search_terms=top_terms,
        )

    def get_marketplace_intelligence(self, date_from, date_to):
        search = self.get_search_analytics(date_from, date_to)
        missing = self.get_missing_parts_report(date_from, date_to)
        dashboard = self.get_executive_dashboard(date_from, date_to)

        # Pre-Gate Corrective #2: status='active' حصرًا (لا out_of_stock) — نفس
        # منطق النسخة الحية تمامًا.
        active_supply_parts = {
            i.get("catalog_part_ref_id") for i in self.inventory_items if i.get("status") == "active"
        }
        zero_active_supply_parts = sum(
            1 for p in self.catalog_parts
            if p.get("status") == "approved" and p.get("id") not in active_supply_parts
        )

        # Pre-Gate Corrective #1: المقام active stores حصرًا.
        active_stores_count = sum(1 for s in self.stores if s.get("status") == "active")
        sellers_to_active_stores_ratio = (
            (dashboard.sellers_total / active_stores_count) if active_stores_count > 0 else 0.0
        )

        return MarketplaceIntelligence(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            demand_hotspot_vehicles=search.top_zero_result_vehicles,
            unmet_demand_parts=missing.top_unmet_demand_parts,
            catalog_parts_with_no_active_supply=zero_active_supply_parts,
            sellers_to_active_stores_ratio=sellers_to_active_stores_ratio,
            request_to_offer_rate=dashboard.request_to_offer_rate,
        )

    def get_trending_parts(self, window_days: int):
        from datetime import timedelta
        now = datetime.utcnow()
        current_from, current_to = now - timedelta(days=window_days), now
        previous_from, previous_to = now - timedelta(days=window_days * 2), now - timedelta(days=window_days)

        current_counts: dict = {}
        previous_counts: dict = {}
        for pr in self.purchase_requests:
            created = pr.get("created_at")
            if created is None:
                continue
            part_id = pr.get("catalog_part_ref_id")
            if current_from <= created < current_to:
                current_counts[part_id] = current_counts.get(part_id, 0) + 1
            elif previous_from <= created < previous_to:
                previous_counts[part_id] = previous_counts.get(part_id, 0) + 1

        all_parts = set(current_counts) | set(previous_counts)
        rows = [
            {
                "catalog_part_ref_id": part_id,
                "current_count": current_counts.get(part_id, 0),
                "previous_count": previous_counts.get(part_id, 0),
                "growth": current_counts.get(part_id, 0) - previous_counts.get(part_id, 0),
            }
            for part_id in all_parts
        ]
        rows.sort(key=lambda r: r["growth"], reverse=True)

        return TrendingParts(
            generated_at_utc=now, window_days=window_days,
            current_period_from=current_from, current_period_to=current_to,
            previous_period_from=previous_from, previous_period_to=previous_to,
            top_growing_parts=rows[:20],
        )

    def get_user_analytics(self, date_from, date_to):
        users_by_role: dict = {}
        users_by_account_type: dict = {}
        verified_sellers_count = 0
        for u in self.users:
            role = u.get("primary_role")
            if role is not None:
                users_by_role[role] = users_by_role.get(role, 0) + 1
            account_type = u.get("account_type")
            if account_type is not None:
                users_by_account_type[account_type] = users_by_account_type.get(account_type, 0) + 1
            if u.get("is_verified_seller"):
                verified_sellers_count += 1

        registrations_by_day = []
        if date_from is not None or date_to is not None:
            by_day: dict = {}
            for u in self.users:
                created = u.get("created_at")
                if created is None:
                    continue
                if date_from is not None and created < date_from:
                    continue
                if date_to is not None and created > date_to:
                    continue
                day = created.date().isoformat()
                by_day[day] = by_day.get(day, 0) + 1
            registrations_by_day = [{"date": d, "count": c} for d, c in sorted(by_day.items())]

        return UserAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            registrations_by_day=registrations_by_day, users_by_role=users_by_role,
            users_by_account_type=users_by_account_type, verified_sellers_count=verified_sellers_count,
        )

    def get_seller_store_analytics(self, date_from, date_to):
        stores_by_status: dict = {}
        for s in self.stores:
            stores_by_status[s["status"]] = stores_by_status.get(s["status"], 0) + 1

        store_owner_ids = {s.get("owner_user_ref_id") for s in self.stores if s.get("owner_user_ref_id") is not None}
        sellers_without_store_count = sum(
            1 for u in self.users
            if u.get("primary_role") in ("individual_seller", "business_seller") and u.get("id") not in store_owner_ids
        )

        stores_with_inventory = {i.get("store_id") for i in self.inventory_items if i.get("store_id") is not None}
        active_stores_without_inventory_count = sum(
            1 for s in self.stores if s.get("status") == "active" and s.get("id") not in stores_with_inventory
        )

        offer_counts: dict = {}
        for o in self.offers:
            key = o.get("seller_store_ref_id")
            if key is not None:
                offer_counts[key] = offer_counts.get(key, 0) + 1
        top_stores_by_offer_count = sorted(
            ({"store_id": k, "offer_count": v} for k, v in offer_counts.items()),
            key=lambda x: x["offer_count"], reverse=True,
        )[:20]

        new_stores_count = 0
        if date_from is not None or date_to is not None:
            new_stores_count = sum(
                1 for s in self.stores
                if s.get("created_at") is not None
                and (date_from is None or s["created_at"] >= date_from)
                and (date_to is None or s["created_at"] <= date_to)
            )

        return SellerStoreAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            stores_by_status=stores_by_status, sellers_without_store_count=sellers_without_store_count,
            active_stores_without_inventory_count=active_stores_without_inventory_count,
            top_stores_by_offer_count=top_stores_by_offer_count, new_stores_count=new_stores_count,
        )

    def get_inventory_catalog_analytics(self, date_from, date_to):
        inventory_items_by_status: dict = {}
        inventory_items_by_pricing_mode: dict = {}
        stale_active_inventory_items_count = 0
        cutoff = datetime.utcnow() - timedelta(days=30)
        for i in self.inventory_items:
            inventory_items_by_status[i["status"]] = inventory_items_by_status.get(i["status"], 0) + 1
            mode = i.get("pricing_mode")
            if mode is not None:
                inventory_items_by_pricing_mode[mode] = inventory_items_by_pricing_mode.get(mode, 0) + 1
            if i["status"] == "active" and i.get("updated_at") is not None and i["updated_at"] < cutoff:
                stale_active_inventory_items_count += 1

        catalog_parts_by_status: dict = {}
        for p in self.catalog_parts:
            catalog_parts_by_status[p["status"]] = catalog_parts_by_status.get(p["status"], 0) + 1

        manufacturers_by_status: dict = {}
        for m in self.manufacturers:
            manufacturers_by_status[m["status"]] = manufacturers_by_status.get(m["status"], 0) + 1

        return InventoryCatalogAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            inventory_items_by_status=inventory_items_by_status,
            inventory_items_by_pricing_mode=inventory_items_by_pricing_mode,
            stale_active_inventory_items_count=stale_active_inventory_items_count,
            catalog_parts_by_status=catalog_parts_by_status, manufacturers_by_status=manufacturers_by_status,
            models_total=len(self.models), generations_total=len(self.generations), trims_total=len(self.trims),
        )

    def get_purchase_request_offer_analytics(self, date_from, date_to):
        offers_by_status: dict = {}
        for o in self.offers:
            offers_by_status[o["status"]] = offers_by_status.get(o["status"], 0) + 1
        withdrawn_offers_count = offers_by_status.get("withdrawn", 0)

        pr_created_at = {pr["id"]: pr.get("created_at") for pr in self.purchase_requests if pr.get("created_at") is not None}
        first_offer_at: dict = {}
        for o in self.offers:
            pr_id = o.get("purchase_request_id")
            created = o.get("created_at")
            if pr_id is None or created is None:
                continue
            if pr_id not in first_offer_at or created < first_offer_at[pr_id]:
                first_offer_at[pr_id] = created

        first_offer_hours = [
            (first_offer_at[pr_id] - pr_created_at[pr_id]).total_seconds() / 3600.0
            for pr_id in first_offer_at if pr_id in pr_created_at
        ]
        avg_hours_to_first_offer = (sum(first_offer_hours) / len(first_offer_hours)) if first_offer_hours else None

        accepted_hours = []
        for o in self.offers:
            if o["status"] != "accepted":
                continue
            pr_id = o.get("purchase_request_id")
            updated = o.get("updated_at")
            if pr_id in pr_created_at and updated is not None:
                accepted_hours.append((updated - pr_created_at[pr_id]).total_seconds() / 3600.0)
        avg_hours_to_accepted_offer = (sum(accepted_hours) / len(accepted_hours)) if accepted_hours else None

        return PurchaseRequestOfferAnalytics(
            generated_at_utc=datetime.utcnow(), date_from=date_from, date_to=date_to,
            offers_by_status=offers_by_status, withdrawn_offers_count=withdrawn_offers_count,
            avg_hours_to_first_offer=avg_hours_to_first_offer,
            avg_hours_to_accepted_offer=avg_hours_to_accepted_offer,
        )

    def get_member_360(self, user_id):
        user_row = next((u for u in self.users if u.get("id") == user_id), None)
        if user_row is None:
            return None

        store_ids = [s["id"] for s in self.stores if s.get("owner_user_ref_id") == user_id]

        sub_row = next((s for s in self.subscriptions if s.get("seller_ref_id") == user_id), None)

        inventory_items_by_status: dict = {}
        for i in self.inventory_items:
            if i.get("store_id") in store_ids:
                inventory_items_by_status[i["status"]] = inventory_items_by_status.get(i["status"], 0) + 1

        purchase_requests_by_status: dict = {}
        for pr in self.purchase_requests:
            if pr.get("buyer_user_ref_id") == user_id:
                purchase_requests_by_status[pr["status"]] = purchase_requests_by_status.get(pr["status"], 0) + 1

        offers_by_status: dict = {}
        for o in self.offers:
            if o.get("seller_store_ref_id") in store_ids:
                offers_by_status[o["status"]] = offers_by_status.get(o["status"], 0) + 1

        conversations_count = len({
            p["conversation_id"] for p in self.conversation_participants if p.get("user_ref_id") == user_id
        })

        support_tickets_by_status: dict = {}
        for t in self.support_tickets:
            if t.get("requester_ref_id") == user_id:
                support_tickets_by_status[t["status"]] = support_tickets_by_status.get(t["status"], 0) + 1

        user_sessions = [s for s in self.sessions if s.get("user_id") == user_id]
        last_login_at = max((s["created_at"] for s in user_sessions), default=None)
        logout_times = [s["revoked_at"] for s in user_sessions if s.get("revoked_reason") == "logout"]
        last_logout_at = max(logout_times, default=None)

        audit_events_as_actor_total = sum(1 for e in self.audit_events if e.get("actor_ref_id") == user_id)

        return Member360(
            generated_at_utc=datetime.utcnow(), user_id=user_row["id"],
            business_code=user_row.get("business_code", ""), primary_role=user_row["primary_role"],
            account_type=user_row.get("account_type", ""), status=user_row["status"],
            created_at=user_row["created_at"], is_verified_seller=user_row.get("is_verified_seller", False),
            store_ids=store_ids,
            subscription_plan_code=sub_row.get("plan_code") if sub_row else None,
            subscription_status=sub_row.get("status") if sub_row else None,
            subscription_expires_at=sub_row.get("expires_at") if sub_row else None,
            inventory_items_total=sum(inventory_items_by_status.values()),
            inventory_items_by_status=inventory_items_by_status,
            purchase_requests_total=sum(purchase_requests_by_status.values()),
            purchase_requests_by_status=purchase_requests_by_status,
            offers_total=sum(offers_by_status.values()), offers_by_status=offers_by_status,
            conversations_count=conversations_count,
            support_tickets_total=sum(support_tickets_by_status.values()),
            support_tickets_by_status=support_tickets_by_status,
            login_sessions_total=len(user_sessions), last_login_at=last_login_at, last_logout_at=last_logout_at,
            audit_events_as_actor_total=audit_events_as_actor_total,
        )

    def get_store_360(self, store_id):
        store_row = next((s for s in self.stores if s.get("id") == store_id), None)
        if store_row is None:
            return None

        owner_id = store_row.get("owner_user_ref_id")
        sub_row = next((s for s in self.subscriptions if s.get("seller_ref_id") == owner_id), None)

        inventory_items_by_status: dict = {}
        for i in self.inventory_items:
            if i.get("store_id") == store_id:
                inventory_items_by_status[i["status"]] = inventory_items_by_status.get(i["status"], 0) + 1

        offers_by_status: dict = {}
        for o in self.offers:
            if o.get("seller_store_ref_id") == store_id:
                offers_by_status[o["status"]] = offers_by_status.get(o["status"], 0) + 1
        offers_total = sum(offers_by_status.values())
        accepted_offers_total = offers_by_status.get("accepted", 0)
        accepted_offer_rate = (accepted_offers_total / offers_total) if offers_total > 0 else 0.0

        pr_created_at = {pr["id"]: pr["created_at"] for pr in self.purchase_requests if pr.get("created_at")}
        response_hours = []
        for o in self.offers:
            if o.get("seller_store_ref_id") != store_id:
                continue
            pr_id = o.get("purchase_request_id")
            updated = o.get("updated_at")
            if pr_id in pr_created_at and updated is not None:
                response_hours.append((updated - pr_created_at[pr_id]).total_seconds() / 3600.0)
        avg_hours_to_offer_response = (sum(response_hours) / len(response_hours)) if response_hours else None

        store_item_ids = {i["id"] for i in self.inventory_items if i.get("store_id") == store_id and i.get("id")}
        media_active_images_total = sum(
            1 for m in self.media_attachments
            if m.get("owner_type") == "inventory_item" and m.get("status") == "active"
            and m.get("owner_ref_id") in store_item_ids
        )

        return Store360(
            generated_at_utc=datetime.utcnow(), store_id=store_row["id"], owner_user_ref_id=owner_id,
            status=store_row["status"], created_at=store_row.get("created_at"),
            subscription_plan_code=sub_row.get("plan_code") if sub_row else None,
            subscription_status=sub_row.get("status") if sub_row else None,
            subscription_expires_at=sub_row.get("expires_at") if sub_row else None,
            inventory_items_total=sum(inventory_items_by_status.values()),
            inventory_items_by_status=inventory_items_by_status,
            offers_total=offers_total, offers_by_status=offers_by_status,
            accepted_offers_total=accepted_offers_total, accepted_offer_rate=accepted_offer_rate,
            avg_hours_to_offer_response=avg_hours_to_offer_response,
            media_active_images_total=media_active_images_total,
        )

    def get_data_quality_dashboard(self):
        inventory_items_without_price = sum(
            1 for i in self.inventory_items
            if i.get("pricing_mode") == "fixed_price" and i.get("price_amount") is None
        )
        item_ids_with_active_image = {
            m["owner_ref_id"] for m in self.media_attachments
            if m.get("owner_type") == "inventory_item" and m.get("status") == "active"
        }
        inventory_items_without_images = sum(
            1 for i in self.inventory_items if i.get("id") not in item_ids_with_active_image
        )

        catalog_parts_proposed_pending_review = sum(1 for c in self.catalog_parts if c.get("status") == "proposed")
        linked_part_ids = {c["catalog_part_ref_id"] for c in self.compatibility_records}
        catalog_parts_not_linked_to_vehicle = sum(
            1 for c in self.catalog_parts if c.get("id") not in linked_part_ids
        )

        return DataQualityDashboard(
            generated_at_utc=datetime.utcnow(),
            inventory_items_without_price=inventory_items_without_price,
            inventory_items_without_images=inventory_items_without_images,
            inventory_items_total=len(self.inventory_items),
            catalog_parts_proposed_pending_review=catalog_parts_proposed_pending_review,
            catalog_parts_not_linked_to_vehicle=catalog_parts_not_linked_to_vehicle,
            catalog_parts_total=len(self.catalog_parts),
        )
