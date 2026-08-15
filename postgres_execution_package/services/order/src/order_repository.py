"""
order_repository.py — طبقة الوصول للبيانات لخدمة الطلبات (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.7؛ 010_pur.sql

مبدأ عدم الحذف الفعلي مطبَّق: لا دالة حذف في هذا الملف؛ الإزالة عبر تغيير
الحالة فقط (cancelled/rejected/withdrawn/expired)، تمامًا كالخدمات السابقة.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
import uuid
from datetime import datetime, timedelta, timezone

from order_service import PurchaseRequest, PurchaseRequestDisplayView, Offer


class OrderRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه order_service.py. لا دالة حذف عمدًا."""

    @abstractmethod
    def insert_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        raise NotImplementedError

    @abstractmethod
    def get_purchase_request_by_id(self, pr_id: str) -> Optional[PurchaseRequest]:
        raise NotImplementedError

    @abstractmethod
    def update_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        raise NotImplementedError

    @abstractmethod
    def insert_offer(self, offer: Offer) -> Offer:
        raise NotImplementedError

    @abstractmethod
    def get_offers_for_purchase_request(self, pr_id: str) -> List[Offer]:
        raise NotImplementedError

    @abstractmethod
    def get_offer_by_id(self, offer_id: str) -> Optional[Offer]:
        raise NotImplementedError

    @abstractmethod
    def update_offer(self, offer: Offer) -> Offer:
        raise NotImplementedError

    # -----------------------------------------------------------------
    # CR-015: طرق قوائم مخصَّصة جديدة بالكامل — لا تعديل على الطرق أعلاه
    # ولا على أي استدعاء قائم لها (منطق الأعمال الحالي كما هو تمامًا).
    # -----------------------------------------------------------------

    @abstractmethod
    def list_purchase_requests_by_buyer(self, buyer_user_ref_id: str, status: Optional[str],
                                         page: int, page_size: int) -> "tuple[List[PurchaseRequest], int]":
        raise NotImplementedError

    @abstractmethod
    def list_purchase_requests_display_for_buyer(self, buyer_user_ref_id: str, page: int,
                                                  page_size: int) -> "tuple[list, int]":
        """
        CR-021: Read Model منفصل تمامًا — لا تعديل على list_purchase_requests_by_buyer
        أعلاه ولا على PurchaseRequestResponse. استعلام واحد مجمَّع (JOINs)
        + استعلام عدّ واحد = عددان ثابتان بغضّ النظر عن N (لا N+1). يُعيد
        كائنات PurchaseRequestDisplayView (منفصلة عن PurchaseRequest).
        """
        raise NotImplementedError

    @abstractmethod
    def get_purchase_request_display_by_id(self, pr_id: str):
        """
        Batch 1 (Offers Integration): نفس Read Model أعلاه لسجل واحد بعينه —
        يُستهلَك من مسار عرض العروض (Offer Display) لإظهار سياق الطلب
        (القطعة/السيارة/الحالة/ملاحظات المشتري) مرة واحدة، لا استعلامًا
        منفصلًا لكل عرض (لا N+1). يُعيد None إن لم يوجد الطلب.
        """
        raise NotImplementedError

    @abstractmethod
    def list_offers_for_purchase_request_paginated(self, pr_id: str, status: Optional[str],
                                                    page: int, page_size: int,
                                                    seller_store_ref_id: Optional[str] = None
                                                    ) -> "tuple[List[Offer], int]":
        raise NotImplementedError

    # -----------------------------------------------------------------
    # Unit 4+5: فجوة حقيقية مكتشَفة (تصفح البائع للطلبات المفتوحة) — لا
    # تعديل على أي طريقة أعلاه ولا على list_purchase_requests_display_for_buyer.
    # -----------------------------------------------------------------

    @abstractmethod
    def list_open_purchase_requests_display(self, page: int, page_size: int) -> "tuple[list, int]":
        """
        نفس Read Model لـlist_purchase_requests_display_for_buyer (PurchaseRequestDisplayView)،
        لكن مُصفّاة لحالة status='open' حصرًا وبلا فلترة بمشترٍ — تصفح عام
        لكل البائعين المسجَّلين (Scoping بالجلسة فقط عند طبقة الـAPI، لا
        فحص دور). ORDER BY created_at DESC (الأحدث أولًا).
        """
        raise NotImplementedError


class PostgresOrderRepository(OrderRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 010_pur.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        business_code = f"PR-{uuid.uuid4().hex[:29]}"
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pur.purchase_requests (business_code, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id, status, condition_ref_id, notes, trim_model_year_ref_id) "
                "VALUES (%(business_code)s, %(buyer_user_ref_id)s, %(catalog_part_ref_id)s, %(trim_ref_id)s, %(status)s, %(condition_ref_id)s, %(notes)s, %(trim_model_year_ref_id)s) RETURNING id, business_code",
                {"business_code": business_code, "buyer_user_ref_id": pr.buyer_user_ref_id,
                 "catalog_part_ref_id": pr.catalog_part_ref_id, "trim_ref_id": pr.trim_ref_id, "status": pr.status,
                 "condition_ref_id": pr.condition_ref_id, "notes": pr.notes,
                 "trim_model_year_ref_id": pr.trim_model_year_ref_id},
            )
            row = cur.fetchone()
            pr.id = row["id"]
            pr.business_code = row["business_code"]
        return pr

    def get_purchase_request_by_id(self, pr_id: str) -> Optional[PurchaseRequest]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id, status, business_code, condition_ref_id, notes, trim_model_year_ref_id "
                "FROM pur.purchase_requests WHERE id = %(id)s",
                {"id": pr_id},
            )
            row = cur.fetchone()
        if row is None:
            return None
        return PurchaseRequest(id=row["id"], buyer_user_ref_id=row["buyer_user_ref_id"],
                                catalog_part_ref_id=row["catalog_part_ref_id"], trim_ref_id=row["trim_ref_id"],
                                status=row["status"], business_code=row["business_code"],
                                condition_ref_id=row["condition_ref_id"], notes=row["notes"],
                                trim_model_year_ref_id=row["trim_model_year_ref_id"])

    def update_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE pur.purchase_requests SET catalog_part_ref_id = %(catalog_part_ref_id)s, "
                    "trim_ref_id = %(trim_ref_id)s, status = %(status)s, updated_at = now() WHERE id = %(id)s",
                    {"catalog_part_ref_id": pr.catalog_part_ref_id, "trim_ref_id": pr.trim_ref_id,
                     "status": pr.status, "id": pr.id},
                )
        return pr

    def insert_offer(self, offer: Offer) -> Offer:
        # يعتمد على uq_offers_one_active_per_seller (فهرس تفرّد جزئي لضمان عدم تكرار العرض النشط)
        business_code = f"OF-{uuid.uuid4().hex[:29]}"
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pur.offers (business_code, purchase_request_id, seller_store_ref_id, amount, currency, "
                "provides_shipping, notes, status) VALUES (%(business_code)s, %(purchase_request_id)s, "
                "%(seller_store_ref_id)s, %(amount)s, %(currency)s, %(provides_shipping)s, %(notes)s, %(status)s) RETURNING id, business_code",
                {"business_code": business_code, "purchase_request_id": offer.purchase_request_id,
                 "seller_store_ref_id": offer.seller_store_ref_id, "amount": offer.amount,
                 "currency": offer.currency, "provides_shipping": offer.provides_shipping,
                 "notes": offer.notes, "status": offer.status},
            )
            row = cur.fetchone()
            offer.id = row["id"]
            offer.business_code = row["business_code"]
        return offer

    def get_offers_for_purchase_request(self, pr_id: str) -> List[Offer]:
        # يعتمد على idx_offers_purchase_request_id
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, purchase_request_id, seller_store_ref_id, amount, currency, provides_shipping, "
                "notes, status, business_code FROM pur.offers WHERE purchase_request_id = %(pr_id)s",
                {"pr_id": pr_id},
            )
            rows = cur.fetchall()
        return [self._row_to_offer(r) for r in rows]

    def get_offer_by_id(self, offer_id: str) -> Optional[Offer]:
        # يعتمد على المفتاح الأساسي
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, purchase_request_id, seller_store_ref_id, amount, currency, provides_shipping, "
                "notes, status, business_code FROM pur.offers WHERE id = %(id)s",
                {"id": offer_id},
            )
            row = cur.fetchone()
        return self._row_to_offer(row) if row else None

    @staticmethod
    def _row_to_offer(row) -> Offer:
        return Offer(id=row["id"], purchase_request_id=row["purchase_request_id"],
                      seller_store_ref_id=row["seller_store_ref_id"], amount=row["amount"], currency=row["currency"],
                      provides_shipping=row["provides_shipping"], notes=row["notes"], status=row["status"],
                      business_code=row["business_code"])

    def update_offer(self, offer: Offer) -> Offer:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE pur.offers SET status = %(status)s, updated_at = now() WHERE id = %(id)s",
                    {"status": offer.status, "id": offer.id},
                )
        return offer

    # -----------------------------------------------------------------
    # CR-015: طرق قوائم جديدة — بلا أي تعديل على الطرق أعلاه
    # -----------------------------------------------------------------

    def list_purchase_requests_by_buyer(self, buyer_user_ref_id: str, status: Optional[str],
                                         page: int, page_size: int):
        # يعتمد على idx_purchase_requests_buyer (وidx_purchase_requests_status عند وجود فلتر)
        offset = (page - 1) * page_size
        filters = ["buyer_user_ref_id = %(buyer_id)s"]
        params = {"buyer_id": buyer_user_ref_id, "limit": page_size, "offset": offset}
        if status is not None:
            filters.append("status = %(status)s")
            params["status"] = status
        where_clause = " AND ".join(filters)
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM pur.purchase_requests WHERE {where_clause}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT id, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id, status, business_code "
                f"FROM pur.purchase_requests WHERE {where_clause} "
                f"ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        items = [PurchaseRequest(id=r["id"], buyer_user_ref_id=r["buyer_user_ref_id"],
                                  catalog_part_ref_id=r["catalog_part_ref_id"], trim_ref_id=r["trim_ref_id"],
                                  status=r["status"], business_code=r["business_code"]) for r in rows]
        return items, total

    def list_purchase_requests_display_for_buyer(self, buyer_user_ref_id: str, page: int, page_size: int):
        """
        CR-021: استعلامان ثابتان فقط (عدّ + بيانات)، بغضّ النظر عن N —
        لا N+1. LATERAL بدل JOIN عادي لأسماء VCT/PCT المحلَّية عمدًا:
        عمود locale حر بلا اصطلاح محسوم بعد (راجع Vehicle Taxonomy
        Fact-Finding)، فقد يوجد أكثر من صف اسم لنفس الكيان — LATERAL
        يضمن صفًا واحدًا فقط فيُمنَع تكرار نفس طلب الشراء في النتيجة
        (سياسة اختيار مؤقتة موثَّقة: تفضيل الصف بلا locale ثم الأقدم
        بالمعرّف؛ قرار نهائي بانتظار قرار نموذج البيانات).
        """
        offset = (page - 1) * page_size
        params = {"buyer_id": buyer_user_ref_id, "limit": page_size, "offset": offset}
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM pur.purchase_requests WHERE buyer_user_ref_id = %(buyer_id)s",
                params,
            )
            total = cur.fetchone()["total"]
            cur.execute(
                """
                SELECT
                    pr.id, pr.business_code, pr.status, pr.created_at,
                    pr.catalog_part_ref_id, pl.name_value AS part_name,
                    pr.trim_ref_id, trln.name_value AS trim_name,
                    mo.id AS model_id, moln.name_value AS model_name,
                    man.id AS manufacturer_id, manln.name_value AS manufacturer_name,
                    gen.id AS generation_id, genln.name_value AS generation_name,
                    pr.trim_model_year_ref_id, tmy.year AS model_year,
                    pr.condition_ref_id, cond.code AS condition_code,
                    pr.notes
                FROM pur.purchase_requests pr
                LEFT JOIN pct.localized_names pl
                    ON pl.catalog_part_id = pr.catalog_part_ref_id AND pl.name_kind = 'canonical'
                LEFT JOIN vct.trims tr ON tr.id = pr.trim_ref_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = tr.id AND owner_type = 'trim'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) trln ON true
                LEFT JOIN vct.generations gen ON gen.id = tr.generation_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = gen.id AND owner_type = 'generation'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) genln ON true
                LEFT JOIN vct.models mo ON mo.id = gen.model_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = mo.id AND owner_type = 'model'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) moln ON true
                LEFT JOIN vct.manufacturers man ON man.id = mo.manufacturer_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = man.id AND owner_type = 'manufacturer'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) manln ON true
                LEFT JOIN vct.trim_model_years tmy ON tmy.id = pr.trim_model_year_ref_id
                LEFT JOIN ref.ref_values cond ON cond.id = pr.condition_ref_id
                WHERE pr.buyer_user_ref_id = %(buyer_id)s
                ORDER BY pr.created_at DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cur.fetchall()
        items = [
            PurchaseRequestDisplayView(
                id=r["id"], business_code=r["business_code"], status=r["status"], created_at=r["created_at"],
                catalog_part_ref_id=r["catalog_part_ref_id"], part_name=r["part_name"],
                trim_ref_id=r["trim_ref_id"], trim_name=r["trim_name"],
                model_id=r["model_id"], model_name=r["model_name"],
                manufacturer_id=r["manufacturer_id"], manufacturer_name=r["manufacturer_name"],
                generation_id=r["generation_id"], generation_name=r["generation_name"],
                trim_model_year_ref_id=r["trim_model_year_ref_id"], model_year=r["model_year"],
                condition_ref_id=r["condition_ref_id"], condition_code=r["condition_code"],
                notes=r["notes"],
            )
            for r in rows
        ]
        return items, total

    _DISPLAY_JOIN_SQL = """
                    pr.id, pr.business_code, pr.status, pr.created_at,
                    pr.catalog_part_ref_id, pl.name_value AS part_name,
                    pr.trim_ref_id, trln.name_value AS trim_name,
                    mo.id AS model_id, moln.name_value AS model_name,
                    man.id AS manufacturer_id, manln.name_value AS manufacturer_name,
                    gen.id AS generation_id, genln.name_value AS generation_name,
                    pr.trim_model_year_ref_id, tmy.year AS model_year,
                    pr.condition_ref_id, cond.code AS condition_code,
                    pr.notes
                FROM pur.purchase_requests pr
                LEFT JOIN pct.localized_names pl
                    ON pl.catalog_part_id = pr.catalog_part_ref_id AND pl.name_kind = 'canonical'
                LEFT JOIN vct.trims tr ON tr.id = pr.trim_ref_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = tr.id AND owner_type = 'trim'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) trln ON true
                LEFT JOIN vct.generations gen ON gen.id = tr.generation_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = gen.id AND owner_type = 'generation'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) genln ON true
                LEFT JOIN vct.models mo ON mo.id = gen.model_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = mo.id AND owner_type = 'model'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) moln ON true
                LEFT JOIN vct.manufacturers man ON man.id = mo.manufacturer_id
                LEFT JOIN LATERAL (
                    SELECT name_value FROM vct.localized_names
                    WHERE owner_ref_id = man.id AND owner_type = 'manufacturer'
                    ORDER BY locale NULLS FIRST, id LIMIT 1
                ) manln ON true
                LEFT JOIN vct.trim_model_years tmy ON tmy.id = pr.trim_model_year_ref_id
                LEFT JOIN ref.ref_values cond ON cond.id = pr.condition_ref_id
    """

    def get_purchase_request_display_by_id(self, pr_id: str):
        """Batch 1 (Offers Integration): نفس JOINs أعلاه (_DISPLAY_JOIN_SQL)، لسجل واحد فقط."""
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT {self._DISPLAY_JOIN_SQL} WHERE pr.id = %(id)s", {"id": pr_id})
            r = cur.fetchone()
        if r is None:
            return None
        return PurchaseRequestDisplayView(
            id=r["id"], business_code=r["business_code"], status=r["status"], created_at=r["created_at"],
            catalog_part_ref_id=r["catalog_part_ref_id"], part_name=r["part_name"],
            trim_ref_id=r["trim_ref_id"], trim_name=r["trim_name"],
            model_id=r["model_id"], model_name=r["model_name"],
            manufacturer_id=r["manufacturer_id"], manufacturer_name=r["manufacturer_name"],
            generation_id=r["generation_id"], generation_name=r["generation_name"],
            trim_model_year_ref_id=r["trim_model_year_ref_id"], model_year=r["model_year"],
            condition_ref_id=r["condition_ref_id"], condition_code=r["condition_code"],
            notes=r["notes"],
        )

    def list_offers_for_purchase_request_paginated(self, pr_id: str, status: Optional[str],
                                                     page: int, page_size: int,
                                                     seller_store_ref_id: Optional[str] = None):
        # يعتمد على idx_offers_purchase_request_id (وidx_offers_status عند وجود فلتر)
        offset = (page - 1) * page_size
        filters = ["purchase_request_id = %(pr_id)s"]
        params = {"pr_id": pr_id, "limit": page_size, "offset": offset}
        if status is not None:
            filters.append("status = %(status)s")
            params["status"] = status
        if seller_store_ref_id is not None:
            filters.append("seller_store_ref_id = %(seller_id)s")
            params["seller_id"] = seller_store_ref_id
        where_clause = " AND ".join(filters)
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM pur.offers WHERE {where_clause}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT id, purchase_request_id, seller_store_ref_id, amount, currency, provides_shipping, "
                f"notes, status, business_code FROM pur.offers WHERE {where_clause} "
                f"ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        return [self._row_to_offer(r) for r in rows], total

    def list_open_purchase_requests_display(self, page: int, page_size: int):
        """
        Unit 4+5 — فجوة حقيقية مكتشَفة: نفس _DISPLAY_JOIN_SQL تمامًا
        (المستخدَم في list_purchase_requests_display_for_buyer وget_purchase_request_display_by_id
        أعلاه)، بلا تعديل عليه، مع WHERE pr.status = 'open' بدل الفلترة
        بمشترٍ. يعتمد على idx_purchase_requests_status.
        """
        offset = (page - 1) * page_size
        params = {"limit": page_size, "offset": offset}
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM pur.purchase_requests WHERE status = 'open'", {}
            )
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT {self._DISPLAY_JOIN_SQL} WHERE pr.status = 'open' "
                f"ORDER BY pr.created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        items = [
            PurchaseRequestDisplayView(
                id=r["id"], business_code=r["business_code"], status=r["status"], created_at=r["created_at"],
                catalog_part_ref_id=r["catalog_part_ref_id"], part_name=r["part_name"],
                trim_ref_id=r["trim_ref_id"], trim_name=r["trim_name"],
                model_id=r["model_id"], model_name=r["model_name"],
                manufacturer_id=r["manufacturer_id"], manufacturer_name=r["manufacturer_name"],
                generation_id=r["generation_id"], generation_name=r["generation_name"],
                trim_model_year_ref_id=r["trim_model_year_ref_id"], model_year=r["model_year"],
                condition_ref_id=r["condition_ref_id"], condition_code=r["condition_code"],
                notes=r["notes"],
            )
            for r in rows
        ]
        return items, total


class InMemoryOrderRepository(OrderRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._prs = {}
        self._offers = {}
        # CR-021: PurchaseRequest لا يحمل created_at إطلاقًا (لا عمود في
        # الـdataclass الأصلي) — ساعة صناعية تُعيد datetime حقيقيًا (لا int)،
        # نفس درس CR-015/رسائل: لا يجوز تمرير عدّاد صحيح مكان datetime.
        self._clock_base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self._clock_ticks = 0
        self._part_names = {}
        # trim_ref_id -> (model_id, model_name, manufacturer_id, manufacturer_name, generation_id, generation_name, trim_name)
        self._trim_vehicle_info = {}
        self._trim_model_years_lookup = {}  # trim_model_year_ref_id -> year
        self._condition_codes = {}          # condition_ref_id -> code
        self._pr_created_at = {}
        self._seq = {"pr": 1, "offer": 1}

    def _next_tick(self):
        self._clock_ticks += 1
        return self._clock_base + timedelta(seconds=self._clock_ticks)

    def set_part_name(self, catalog_part_ref_id, name):
        self._part_names[catalog_part_ref_id] = name

    def set_trim_vehicle_info(self, trim_ref_id, model_id, model_name, manufacturer_id, manufacturer_name,
                               generation_id=None, generation_name=None, trim_name=None):
        self._trim_vehicle_info[trim_ref_id] = (
            model_id, model_name, manufacturer_id, manufacturer_name, generation_id, generation_name, trim_name,
        )

    def set_trim_model_year(self, trim_model_year_ref_id, year):
        """Batch 1: اختباري فقط — يحاكي حل model_year في Display Projection."""
        self._trim_model_years_lookup[trim_model_year_ref_id] = year

    def set_condition_code(self, condition_ref_id, code):
        """Batch 1: اختباري فقط — يحاكي حل condition_code في Display Projection."""
        self._condition_codes[condition_ref_id] = code

    def insert_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        pr.id = f"pr-{self._seq['pr']}"
        pr.business_code = f"PR-{uuid.uuid4().hex[:29]}"
        self._seq["pr"] += 1
        self._prs[pr.id] = pr
        self._pr_created_at[pr.id] = self._next_tick()  # CR-021: لا عمود created_at على PurchaseRequest نفسها
        return pr

    def get_purchase_request_by_id(self, pr_id: str) -> Optional[PurchaseRequest]:
        return self._prs.get(pr_id)

    def update_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        self._prs[pr.id] = pr
        return pr

    def insert_offer(self, offer: Offer) -> Offer:
        offer.id = f"offer-{self._seq['offer']}"
        offer.business_code = f"OF-{uuid.uuid4().hex[:29]}"
        self._seq["offer"] += 1
        self._offers[offer.id] = offer
        return offer

    def get_offers_for_purchase_request(self, pr_id: str) -> List[Offer]:
        return [o for o in self._offers.values() if o.purchase_request_id == pr_id]

    def get_offer_by_id(self, offer_id: str) -> Optional[Offer]:
        return self._offers.get(offer_id)

    def update_offer(self, offer: Offer) -> Offer:
        self._offers[offer.id] = offer
        return offer

    # -----------------------------------------------------------------
    # CR-015: طرق قوائم جديدة — محاكاة في الذاكرة للاختبارات فقط
    # (ترتيب الإدراج في القاموس يُستخدَم كبديل لـcreated_at DESC)
    # -----------------------------------------------------------------

    def list_purchase_requests_by_buyer(self, buyer_user_ref_id: str, status: Optional[str],
                                         page: int, page_size: int):
        items = [pr for pr in reversed(list(self._prs.values())) if pr.buyer_user_ref_id == buyer_user_ref_id]
        if status is not None:
            items = [pr for pr in items if pr.status == status]
        total = len(items)
        start = (page - 1) * page_size
        return items[start:start + page_size], total

    def list_purchase_requests_display_for_buyer(self, buyer_user_ref_id: str, page: int, page_size: int):
        prs = [pr for pr in self._prs.values() if pr.buyer_user_ref_id == buyer_user_ref_id]
        prs.sort(key=lambda pr: self._pr_created_at.get(pr.id, self._clock_base), reverse=True)
        total = len(prs)
        start = (page - 1) * page_size
        page_prs = prs[start:start + page_size]

        items = []
        for pr in page_prs:
            model_id = model_name = manufacturer_id = manufacturer_name = None
            generation_id = generation_name = trim_name = None
            if pr.trim_ref_id in self._trim_vehicle_info:
                (model_id, model_name, manufacturer_id, manufacturer_name,
                 generation_id, generation_name, trim_name) = self._trim_vehicle_info[pr.trim_ref_id]
            model_year = self._trim_model_years_lookup.get(pr.trim_model_year_ref_id) \
                if pr.trim_model_year_ref_id else None
            condition_code = self._condition_codes.get(pr.condition_ref_id) if pr.condition_ref_id else None
            items.append(PurchaseRequestDisplayView(
                id=pr.id, business_code=pr.business_code, status=pr.status,
                created_at=self._pr_created_at.get(pr.id, self._clock_base),
                catalog_part_ref_id=pr.catalog_part_ref_id,
                part_name=self._part_names.get(pr.catalog_part_ref_id),
                trim_ref_id=pr.trim_ref_id, trim_name=trim_name,
                model_id=model_id, model_name=model_name,
                manufacturer_id=manufacturer_id, manufacturer_name=manufacturer_name,
                generation_id=generation_id, generation_name=generation_name,
                trim_model_year_ref_id=pr.trim_model_year_ref_id, model_year=model_year,
                condition_ref_id=pr.condition_ref_id, condition_code=condition_code,
                notes=pr.notes,
            ))
        return items, total

    def get_purchase_request_display_by_id(self, pr_id: str):
        """Batch 1 (Offers Integration): نفس منطق list_purchase_requests_display_for_buyer، لسجل واحد."""
        pr = self._prs.get(pr_id)
        if pr is None:
            return None
        model_id = model_name = manufacturer_id = manufacturer_name = None
        generation_id = generation_name = trim_name = None
        if pr.trim_ref_id in self._trim_vehicle_info:
            (model_id, model_name, manufacturer_id, manufacturer_name,
             generation_id, generation_name, trim_name) = self._trim_vehicle_info[pr.trim_ref_id]
        model_year = self._trim_model_years_lookup.get(pr.trim_model_year_ref_id) \
            if pr.trim_model_year_ref_id else None
        condition_code = self._condition_codes.get(pr.condition_ref_id) if pr.condition_ref_id else None
        return PurchaseRequestDisplayView(
            id=pr.id, business_code=pr.business_code, status=pr.status,
            created_at=self._pr_created_at.get(pr.id, self._clock_base),
            catalog_part_ref_id=pr.catalog_part_ref_id,
            part_name=self._part_names.get(pr.catalog_part_ref_id),
            trim_ref_id=pr.trim_ref_id, trim_name=trim_name,
            model_id=model_id, model_name=model_name,
            manufacturer_id=manufacturer_id, manufacturer_name=manufacturer_name,
            generation_id=generation_id, generation_name=generation_name,
            trim_model_year_ref_id=pr.trim_model_year_ref_id, model_year=model_year,
            condition_ref_id=pr.condition_ref_id, condition_code=condition_code,
            notes=pr.notes,
        )

    def list_offers_for_purchase_request_paginated(self, pr_id: str, status: Optional[str],
                                                     page: int, page_size: int,
                                                     seller_store_ref_id: Optional[str] = None):
        items = [o for o in reversed(list(self._offers.values())) if o.purchase_request_id == pr_id]
        if status is not None:
            items = [o for o in items if o.status == status]
        if seller_store_ref_id is not None:
            items = [o for o in items if o.seller_store_ref_id == seller_store_ref_id]
        total = len(items)
        start = (page - 1) * page_size
        return items[start:start + page_size], total

    def list_open_purchase_requests_display(self, page: int, page_size: int):
        """Unit 4+5: نفس منطق list_purchase_requests_display_for_buyer أعلاه حرفيًا، بلا فلترة بمشترٍ، مُصفّاة لـstatus='open' فقط."""
        prs = [pr for pr in self._prs.values() if pr.status == "open"]
        prs.sort(key=lambda pr: self._pr_created_at.get(pr.id, self._clock_base), reverse=True)
        total = len(prs)
        start = (page - 1) * page_size
        page_prs = prs[start:start + page_size]

        items = []
        for pr in page_prs:
            model_id = model_name = manufacturer_id = manufacturer_name = None
            generation_id = generation_name = trim_name = None
            if pr.trim_ref_id in self._trim_vehicle_info:
                (model_id, model_name, manufacturer_id, manufacturer_name,
                 generation_id, generation_name, trim_name) = self._trim_vehicle_info[pr.trim_ref_id]
            model_year = self._trim_model_years_lookup.get(pr.trim_model_year_ref_id) \
                if pr.trim_model_year_ref_id else None
            condition_code = self._condition_codes.get(pr.condition_ref_id) if pr.condition_ref_id else None
            items.append(PurchaseRequestDisplayView(
                id=pr.id, business_code=pr.business_code, status=pr.status,
                created_at=self._pr_created_at.get(pr.id, self._clock_base),
                catalog_part_ref_id=pr.catalog_part_ref_id,
                part_name=self._part_names.get(pr.catalog_part_ref_id),
                trim_ref_id=pr.trim_ref_id, trim_name=trim_name,
                model_id=model_id, model_name=model_name,
                manufacturer_id=manufacturer_id, manufacturer_name=manufacturer_name,
                generation_id=generation_id, generation_name=generation_name,
                trim_model_year_ref_id=pr.trim_model_year_ref_id, model_year=model_year,
                condition_ref_id=pr.condition_ref_id, condition_code=condition_code,
                notes=pr.notes,
            ))
        return items, total
