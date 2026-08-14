"""
sub_service.py — منطق خدمة الاشتراكات (SUB)
المرجع: REQ-SUB-001..005 (يشمل 004-A/B/C، 005-A)؛ CR-014 (خطة Free الدائمة)

ملاحظة نطاق: مدة الاشتراك (expires_at) غير مُشتقَّة من أي مصدر بيانات آخر
(لا عمود "مدة" على sub.plans نفسها — الخطة تشير فقط لـref.ref_values عبر
plan_type_ref_id)؛ تُحسَب من duration_days يُمرَّرها الطالب عند الاشتراك،
قرار تنفيذي بسيط لغياب مواصفة أدق في REQ-SUB-002. الاستثناء الوحيد: خطة
Free النظامية (CR-014) لا مدة لها إطلاقًا (expires_at = NULL دائمًا).

انتهاء الاشتراك (REQ-SUB-004) يُطبَّق بفحص كسول (Lazy Check) عند كل قراءة/
كتابة تلمس الاشتراك، لا عبر مهمة مجدوَلة منفصلة في هذا الإصدار — أبسط
وكافٍ لضمان السلوك المطلوب دون الاعتماد على تشغيل Scheduler دوري فعليًا.

CR-014 — عضوية Free دائمة لكل بائع:
- كل بائع يملك اشتراكًا فعالًا دومًا؛ لا وجود لحالة "بلا اشتراك على الإطلاق"
  من منظور واجهة البرمجة — أول استعلام لبائع جديد يُنشئ له اشتراك Free تلقائيًا.
- انتهاء خطة مدفوعة (Lazy Check) يُعيد البائع تلقائيًا لخطة Free، لا إلى
  حالة "منتهي" مسدودة؛ الحالة (status) تبقى 'active' دومًا بعد هذا التحويل.
- الاشتراك في خطة مدفوعة جديدة أثناء وجود اشتراك Free نشط (وهو الوضع
  الافتراضي لأي بائع) ليس "اشتراكًا مكررًا" — إنه ترقية، ويُسمح به دومًا.
  التعارض (409 ALREADY_SUBSCRIBED) يقع فقط عند وجود خطة مدفوعة أخرى نشطة
  بالفعل وغير منتهية.
- حدود/مزايا كل خطة (REQ-SUB الخاصة بها) والرفض عند تجاوزها يُدارا خارج
  نطاق هذه الوحدة، على مستوى الخدمات المستهلِكة (Inventory/Orders/إلخ)؛
  هذا الملف مسؤول فقط عن ضمان استمرار وجود اشتراك فعال دومًا (Free كحد أدنى)
  وعن دورة حياة الترقية/التخفيض بين الخطط.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


SUBSCRIPTION_STATUSES = {"active", "expired"}


@dataclass
class Plan:
    id: str
    plan_type_ref_id: str
    is_free: bool = False


@dataclass
class SellerSubscription:
    id: str
    seller_ref_id: str
    plan_id: str
    status: str = "active"
    expires_at: Optional[datetime] = None


class SellerAlreadySubscribedError(Exception):
    """REQ-SUB-002: بائع واحد باشتراك مدفوع نشط واحد فقط في لحظة معيَّنة.
    لا ينطبق على الاشتراك في Free نفسها، ولا يمنع الترقية من Free إلى مدفوعة."""


class InvalidDurationError(Exception):
    """مدة الاشتراك يجب أن تكون موجبة."""


def create_plan(plan_type_ref_id: str) -> Plan:
    if not plan_type_ref_id or not plan_type_ref_id.strip():
        raise ValueError("plan_type_ref_id يجب ألا يكون فارغًا.")
    return Plan(id="", plan_type_ref_id=plan_type_ref_id)


def compute_expiry(now: datetime, duration_days: int) -> datetime:
    if duration_days <= 0:
        raise InvalidDurationError("مدة الاشتراك يجب أن تكون أكبر من صفر يومًا.")
    return now + timedelta(days=duration_days)


def subscribe_seller(seller_ref_id: str, plan_id: str, existing_active_subscription: Optional[SellerSubscription],
                      now: datetime, duration_days: int, free_plan_id: Optional[str] = None) -> SellerSubscription:
    if existing_active_subscription is not None and not is_expired(existing_active_subscription, now):
        # اشتراك حالي على خطة مدفوعة فعلًا (ليست Free) وما زال ساريًا => تعارض حقيقي.
        # أما إن كان الاشتراك الحالي هو Free نفسها، فهذه ترقية مسموحة دومًا.
        if free_plan_id is None or existing_active_subscription.plan_id != free_plan_id:
            raise SellerAlreadySubscribedError("يوجد بالفعل اشتراك مدفوع نشط لهذا البائع.")

    if free_plan_id is not None and plan_id == free_plan_id:
        expires_at = None  # خطة Free لا تنتهي أبدًا
    else:
        expires_at = compute_expiry(now, duration_days)

    return SellerSubscription(id="", seller_ref_id=seller_ref_id, plan_id=plan_id, expires_at=expires_at)


def is_expired(subscription: SellerSubscription, now: datetime) -> bool:
    return subscription.expires_at is not None and now >= subscription.expires_at


def apply_lazy_expiry(subscription: SellerSubscription, now: datetime,
                       free_plan_id: Optional[str] = None) -> SellerSubscription:
    """REQ-SUB-004 + CR-014: يُستدعى قبل أي استهلاك لحالة الاشتراك. عند انتهاء
    خطة مدفوعة، يعود البائع تلقائيًا لخطة Free (الحالة تبقى 'active')، بدل
    الانتقال لحالة 'expired' مسدودة. لا أثر إن كان نشطًا فعليًا، أو كان
    أصلًا على خطة Free (لا تنتهي)."""
    if subscription.status == "active" and is_expired(subscription, now):
        if free_plan_id is not None:
            subscription.plan_id = free_plan_id
            subscription.expires_at = None
        else:
            # تحوّط دفاعي فقط: لا ينبغي الوصول لهذا المسار في بيئة مهاجَرة
            # بالكامل (026_sub_free_plan.sql يبذر خطة Free دومًا).
            subscription.status = "expired"
    return subscription


def change_plan(subscription: SellerSubscription, new_plan_id: str, now: datetime,
                 free_plan_id: Optional[str] = None) -> SellerSubscription:
    """REQ-SUB-005/005-A: سريان فوري؛ لا يجوز تغيير خطة اشتراك منتهٍ (يجب
    الاشتراك من جديد بدلاً من ذلك). التخفيض الصريح لخطة Free يُصفِّر
    expires_at (CR-014)."""
    apply_lazy_expiry(subscription, now, free_plan_id)
    if subscription.status != "active":
        raise ValueError("لا يجوز تغيير خطة اشتراك غير نشط؛ يلزم اشتراك جديد.")
    subscription.plan_id = new_plan_id
    if free_plan_id is not None and new_plan_id == free_plan_id:
        subscription.expires_at = None
    return subscription


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository
# ---------------------------------------------------------------------------

def create_plan_via_repository(repository, plan_type_ref_id: str) -> Plan:
    plan = create_plan(plan_type_ref_id)
    return repository.insert_plan(plan)


def _free_plan_id(repository) -> Optional[str]:
    free_plan = repository.get_free_plan()
    return free_plan.id if free_plan is not None else None


def subscribe_seller_via_repository(repository, seller_ref_id: str, plan_id: str,
                                     now: datetime, duration_days: int) -> SellerSubscription:
    existing = repository.get_active_subscription_for_seller(seller_ref_id)
    free_plan_id = _free_plan_id(repository)
    subscription = subscribe_seller(seller_ref_id, plan_id, existing, now, duration_days, free_plan_id)
    return repository.insert_subscription(subscription)


def get_my_subscription_via_repository(repository, seller_ref_id: str, now: datetime) -> Optional[SellerSubscription]:
    """CR-014: كل بائع يملك اشتراكًا فعالًا دومًا. أول استعلام لبائع لم
    يشترك قط يُنشئ له اشتراك Free تلقائيًا بدل إعادة None."""
    subscription = repository.get_active_subscription_for_seller(seller_ref_id)
    free_plan = repository.get_free_plan()
    free_plan_id = free_plan.id if free_plan is not None else None

    if subscription is None:
        if free_plan_id is None:
            return None  # لم تُبذر خطة Free بعد (لا ينبغي حدوثه بعد 026)؛ تحوّط فقط
        new_subscription = SellerSubscription(id="", seller_ref_id=seller_ref_id, plan_id=free_plan_id,
                                               status="active", expires_at=None)
        return repository.insert_subscription(new_subscription)

    apply_lazy_expiry(subscription, now, free_plan_id)
    repository.update_subscription(subscription)
    return subscription


def change_plan_via_repository(repository, subscription_id: str, new_plan_id: str, now: datetime) -> SellerSubscription:
    subscription = repository.get_subscription_by_id(subscription_id)
    if subscription is None:
        raise ValueError(f"لا يوجد اشتراك بالمعرّف: {subscription_id}")
    free_plan_id = _free_plan_id(repository)
    change_plan(subscription, new_plan_id, now, free_plan_id)
    return repository.update_subscription(subscription)
