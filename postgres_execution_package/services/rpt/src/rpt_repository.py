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


class RptRepository(ABC):
    """Read-Only بالكامل — لا أي abstractmethod للكتابة، عمدًا، لأن هذا Domain
    لا يملك بيانات، يقرأ فقط من Domains أخرى."""

    @abstractmethod
    def get_executive_dashboard(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> ExecutiveDashboard:
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


class InMemoryRptRepository(RptRepository):
    """للاختبارات فقط. لا يحاكي Repositories الأخرى — يقبل بيانات خام مُدخَلة
    مباشرة (قوائم قواميس تمثّل صفوف الجداول) لعزل اختبار منطق التجميع/الصيغ
    هنا عن أي اعتماد على Domains أخرى، تمامًا كما توصي به طريقة SSOT أعلاه."""

    def __init__(self):
        self.users: list[dict] = []           # {"status": str, "primary_role": str, "created_at": datetime}
        self.stores: list[dict] = []           # {"status": str}
        self.inventory_items: list[dict] = []  # {"status": str}
        self.catalog_parts: list[dict] = []    # {"status": str}
        self.purchase_requests: list[dict] = []  # {"id": str, "status": str}
        self.offers: list[dict] = []           # {"purchase_request_id": str, "status": str}
        self.subscriptions: list[dict] = []    # {"status": str, "plan_code": str}

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
