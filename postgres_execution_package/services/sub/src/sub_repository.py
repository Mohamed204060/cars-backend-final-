"""
sub_repository.py — طبقة الوصول للبيانات لخدمة الاشتراكات (SUB)
المرجع: دليل حوكمة التنفيذ v1.7؛ 008_sub.sql
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from sub_service import Plan, SellerSubscription


class SubRepository(ABC):

    @abstractmethod
    def insert_plan(self, plan: Plan) -> Plan:
        raise NotImplementedError

    @abstractmethod
    def get_all_plans(self) -> List[Plan]:
        raise NotImplementedError

    @abstractmethod
    def get_free_plan(self) -> Optional[Plan]:
        """CR-014: يُعيد خطة Free النظامية الوحيدة، أو None إن لم تُبذر بعد."""
        raise NotImplementedError

    @abstractmethod
    def insert_subscription(self, subscription: SellerSubscription) -> SellerSubscription:
        raise NotImplementedError

    @abstractmethod
    def get_subscription_by_id(self, subscription_id: str) -> Optional[SellerSubscription]:
        raise NotImplementedError

    @abstractmethod
    def get_active_subscription_for_seller(self, seller_ref_id: str) -> Optional[SellerSubscription]:
        """يُعيد أحدث اشتراك (نشط أو منتهٍ حديثًا) لهذا البائع، أو None إن لم يشترك قط."""
        raise NotImplementedError

    @abstractmethod
    def update_subscription(self, subscription: SellerSubscription) -> SellerSubscription:
        raise NotImplementedError


class PostgresSubRepository(SubRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 008_sub.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_plan(self, plan: Plan) -> Plan:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO sub.plans (plan_type_ref_id) VALUES (%(plan_type_ref_id)s) RETURNING id",
                {"plan_type_ref_id": plan.plan_type_ref_id},
            )
            plan.id = cur.fetchone()["id"]
        return plan

    def get_all_plans(self) -> List[Plan]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, plan_type_ref_id, is_free FROM sub.plans")
            rows = cur.fetchall()
        return [Plan(id=r["id"], plan_type_ref_id=r["plan_type_ref_id"], is_free=r["is_free"]) for r in rows]

    def get_free_plan(self) -> Optional[Plan]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, plan_type_ref_id, is_free FROM sub.plans WHERE is_free = true LIMIT 1")
            row = cur.fetchone()
        return Plan(id=row["id"], plan_type_ref_id=row["plan_type_ref_id"], is_free=row["is_free"]) if row else None

    def insert_subscription(self, subscription: SellerSubscription) -> SellerSubscription:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO sub.seller_subscriptions (seller_ref_id, plan_id, status, expires_at) "
                "VALUES (%(seller_ref_id)s, %(plan_id)s, %(status)s, %(expires_at)s) RETURNING id",
                {"seller_ref_id": subscription.seller_ref_id, "plan_id": subscription.plan_id,
                 "status": subscription.status, "expires_at": subscription.expires_at},
            )
            subscription.id = cur.fetchone()["id"]
        return subscription

    def get_subscription_by_id(self, subscription_id: str) -> Optional[SellerSubscription]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, seller_ref_id, plan_id, status, expires_at FROM sub.seller_subscriptions WHERE id = %(id)s",
                {"id": subscription_id},
            )
            row = cur.fetchone()
        return self._row_to_subscription(row) if row else None

    def get_active_subscription_for_seller(self, seller_ref_id: str) -> Optional[SellerSubscription]:
        # يعتمد على idx_seller_subscriptions_seller؛ أحدث اشتراك بغض النظر عن الحالة
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, seller_ref_id, plan_id, status, expires_at FROM sub.seller_subscriptions "
                "WHERE seller_ref_id = %(seller_ref_id)s ORDER BY created_at DESC LIMIT 1",
                {"seller_ref_id": seller_ref_id},
            )
            row = cur.fetchone()
        return self._row_to_subscription(row) if row else None

    @staticmethod
    def _row_to_subscription(row) -> SellerSubscription:
        return SellerSubscription(id=row["id"], seller_ref_id=row["seller_ref_id"], plan_id=row["plan_id"],
                                   status=row["status"], expires_at=row["expires_at"])

    def update_subscription(self, subscription: SellerSubscription) -> SellerSubscription:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE sub.seller_subscriptions SET plan_id = %(plan_id)s, status = %(status)s, "
                    "expires_at = %(expires_at)s, updated_at = now() WHERE id = %(id)s",
                    {"plan_id": subscription.plan_id, "status": subscription.status,
                     "expires_at": subscription.expires_at, "id": subscription.id},
                )
        return subscription


class InMemorySubRepository(SubRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._plans = {}
        self._subscriptions = {}
        self._seq = {"plan": 1, "sub": 1}
        # CR-014: خطة Free النظامية مبذورة دومًا، أسوة بـ026_sub_free_plan.sql
        free_plan = Plan(id="plan-free", plan_type_ref_id="free", is_free=True)
        self._plans[free_plan.id] = free_plan

    def insert_plan(self, plan: Plan) -> Plan:
        plan.id = f"plan-{self._seq['plan']}"
        self._seq["plan"] += 1
        self._plans[plan.id] = plan
        return plan

    def get_all_plans(self) -> List[Plan]:
        return list(self._plans.values())

    def get_free_plan(self) -> Optional[Plan]:
        for plan in self._plans.values():
            if plan.is_free:
                return plan
        return None

    def insert_subscription(self, subscription: SellerSubscription) -> SellerSubscription:
        subscription.id = f"sub-{self._seq['sub']}"
        self._seq["sub"] += 1
        self._subscriptions[subscription.id] = subscription
        return subscription

    def get_subscription_by_id(self, subscription_id: str) -> Optional[SellerSubscription]:
        return self._subscriptions.get(subscription_id)

    def get_active_subscription_for_seller(self, seller_ref_id: str) -> Optional[SellerSubscription]:
        matches = [s for s in self._subscriptions.values() if s.seller_ref_id == seller_ref_id]
        return matches[-1] if matches else None

    def update_subscription(self, subscription: SellerSubscription) -> SellerSubscription:
        self._subscriptions[subscription.id] = subscription
        return subscription
