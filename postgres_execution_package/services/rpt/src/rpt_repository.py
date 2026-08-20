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
from datetime import datetime
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


class InMemoryRptRepository(RptRepository):
    """للاختبارات فقط. لا يحاكي Repositories الأخرى — يقبل بيانات خام مُدخَلة
    مباشرة (قوائم قواميس تمثّل صفوف الجداول) لعزل اختبار منطق التجميع/الصيغ
    هنا عن أي اعتماد على Domains أخرى، تمامًا كما توصي به طريقة SSOT أعلاه."""

    def __init__(self):
        self.users: list[dict] = []           # {"status": str, "primary_role": str, "created_at": datetime}
        self.stores: list[dict] = []           # {"status": str}
        self.inventory_items: list[dict] = []  # {"status": str, "catalog_part_ref_id": str (اختياري، لـMarketplace Intelligence فقط)}
        self.catalog_parts: list[dict] = []    # {"status": str, "id": str (اختياري، لـMarketplace Intelligence فقط)}
        self.purchase_requests: list[dict] = []  # {"id": str, "status": str, "catalog_part_ref_id": str, "created_at": datetime (اختياري، لـTrending Parts فقط)}
        self.offers: list[dict] = []           # {"purchase_request_id": str, "status": str}
        self.subscriptions: list[dict] = []    # {"status": str, "plan_code": str}
        self.ana_events: list[dict] = []       # {"event_type": str, "occurred_at_utc": datetime, "metadata": dict}

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
