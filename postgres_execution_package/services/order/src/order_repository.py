"""
order_repository.py — طبقة الوصول للبيانات لخدمة الطلبات (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.7؛ 010_pur.sql

مبدأ عدم الحذف الفعلي مطبَّق: لا دالة حذف في هذا الملف؛ الإزالة عبر تغيير
الحالة فقط (cancelled/rejected/withdrawn/expired)، تمامًا كالخدمات السابقة.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
import uuid

from order_service import PurchaseRequest, Offer


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
    def list_offers_for_purchase_request_paginated(self, pr_id: str, status: Optional[str],
                                                    page: int, page_size: int,
                                                    seller_store_ref_id: Optional[str] = None
                                                    ) -> "tuple[List[Offer], int]":
        raise NotImplementedError


class PostgresOrderRepository(OrderRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 010_pur.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        business_code = f"PR-{uuid.uuid4().hex[:29]}"
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pur.purchase_requests (business_code, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id, status) "
                "VALUES (%(business_code)s, %(buyer_user_ref_id)s, %(catalog_part_ref_id)s, %(trim_ref_id)s, %(status)s) RETURNING id, business_code",
                {"business_code": business_code, "buyer_user_ref_id": pr.buyer_user_ref_id,
                 "catalog_part_ref_id": pr.catalog_part_ref_id, "trim_ref_id": pr.trim_ref_id, "status": pr.status},
            )
            row = cur.fetchone()
            pr.id = row["id"]
            pr.business_code = row["business_code"]
        return pr

    def get_purchase_request_by_id(self, pr_id: str) -> Optional[PurchaseRequest]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id, status, business_code "
                "FROM pur.purchase_requests WHERE id = %(id)s",
                {"id": pr_id},
            )
            row = cur.fetchone()
        if row is None:
            return None
        return PurchaseRequest(id=row["id"], buyer_user_ref_id=row["buyer_user_ref_id"],
                                catalog_part_ref_id=row["catalog_part_ref_id"], trim_ref_id=row["trim_ref_id"],
                                status=row["status"], business_code=row["business_code"])

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


class InMemoryOrderRepository(OrderRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._prs = {}
        self._offers = {}
        self._seq = {"pr": 1, "offer": 1}

    def insert_purchase_request(self, pr: PurchaseRequest) -> PurchaseRequest:
        pr.id = f"pr-{self._seq['pr']}"
        pr.business_code = f"PR-{uuid.uuid4().hex[:29]}"
        self._seq["pr"] += 1
        self._prs[pr.id] = pr
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
