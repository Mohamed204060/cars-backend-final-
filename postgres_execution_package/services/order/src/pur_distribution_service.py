"""
pur_distribution_service.py — توسعة مجال طلبات الشراء (PUR) وفق CR-009
المرجع: SRS الحزمة D v1.6 (REQ-PUR-019..022)؛ ADR-035 (Platform Scheduler)

يُبنى فوق order_service.py الموجودة دون تعديلها (منطق مُختبَر فعليًا بـ26
اختبارًا ناجحًا)؛ هذا الملف يضيف التوزيع القائم على القواعد الصريحة، تكامل
المُجدوِل المشترك، والتذكيرات عبر Dependency Injection مع NTF.

مبدأ SSOT: SellerProfile يشير لمتجر (STR) بمعرّف مرجعي فقط، لا نسخ بياناته.
مبدأ عدم "الذكاء": كل قاعدة توزيع صريحة وقابلة للتفسير الكامل؛ لا ترجيح ولا
تعلُّم آلي في هذا الإصدار (قرار معتمَد صراحة في CR-009).
"""

from dataclasses import dataclass, field
from typing import Optional, List, Callable
from datetime import datetime


@dataclass
class SellerProfile:
    """SSOT: تمثيل مبسَّط لمعايير توزيع فقط؛ لا بيانات متجر كاملة، مجرد مرجع."""
    store_ref_id: str
    city_ref_id: str
    specialty_ref_ids: List[str] = field(default_factory=list)  # فئات القطع التي يتخصَّص بها
    activity_type: str = "active"       # active | inactive
    status: str = "approved"            # approved | pending | suspended | rejected
    is_ready_to_receive: bool = True


@dataclass
class DistributionCriteria:
    """معايير التوزيع المشتقَّة من طلب الشراء نفسه (مدينة مستهدَفة + تخصص القطعة)."""
    city_ref_id: str
    specialty_ref_id: str


class InvalidDistributionCriteriaError(Exception):
    """معايير توزيع غير مكتملة أو غير صالحة."""


# ---------------------------------------------------------------------------
# REQ-PUR-019: التوزيع القائم على قواعد صريحة (لا ذكاء اصطناعي)
# ---------------------------------------------------------------------------

def is_seller_eligible_for_distribution(seller: SellerProfile, criteria: DistributionCriteria) -> bool:
    """
    كل قاعدة هنا صريحة وقابلة للتفسير الكامل: مطابقة المدينة، مطابقة
    التخصص، نشاط البائع، حالة اعتماده، وجاهزيته لاستقبال طلبات جديدة.
    لا وزن، لا تقييم مرجَّح، لا تعلُّم آلي — تحقق منطقي AND صريح فقط.
    """
    if seller.city_ref_id != criteria.city_ref_id:
        return False
    if criteria.specialty_ref_id not in seller.specialty_ref_ids:
        return False
    if seller.activity_type != "active":
        return False
    if seller.status != "approved":
        return False
    if not seller.is_ready_to_receive:
        return False
    return True


def distribute_purchase_request(criteria: DistributionCriteria, candidate_sellers: List[SellerProfile]) -> List[str]:
    """يُعيد قائمة معرّفات المتاجر المؤهَّلة فقط (SSOT: مراجع لا بيانات)."""
    if not criteria.city_ref_id or not criteria.specialty_ref_id:
        raise InvalidDistributionCriteriaError("معايير التوزيع (المدينة والتخصص) إلزامية.")
    return [s.store_ref_id for s in candidate_sellers if is_seller_eligible_for_distribution(s, criteria)]


# ---------------------------------------------------------------------------
# REQ-PUR-020: تكامل مع Platform Scheduler لانتهاء صلاحية الطلبات
# ---------------------------------------------------------------------------

def schedule_purchase_request_expiration(pr_id: str, expires_at: datetime, schedule_job_fn: Callable):
    """
    schedule_job_fn دالة محقونة من scheduler_service (عبر Repository)؛ PUR
    لا تنفِّذ أي منطق جدولة داخلي خاص بها، بل تستدعي المُجدوِل المشترك حصرًا.
    """
    return schedule_job_fn(job_type="pur_expiration_check", target_ref_id=pr_id, scheduled_at=expires_at)


def execute_expiration_check(pr, transition_status_fn) -> bool:
    """
    دالة التنفيذ التي يستدعيها المُجدوِل المشترك عبر execute_job_fn المحقونة
    (لا معرفة للمُجدوِل بمحتواها)؛ transition_status_fn محقونة أيضًا من
    order_service.py (transition_purchase_request_status) لتفادي أي استيراد
    مباشر غير ضروري هنا. تُعيد True عند النجاح.
    """
    if pr.status not in {"open", "under_review"}:
        return True  # لا شيء لفعله؛ الطلب أُغلِق فعليًا لسبب آخر — ليس فشلاً
    transition_status_fn(pr, "expired")
    return True


# ---------------------------------------------------------------------------
# REQ-PUR-021: تذكيرات تلقائية عبر Dependency Injection (PUR تبادر، لا NTF تشترك)
# ---------------------------------------------------------------------------

def send_seller_reminder(pr_id: str, seller_store_ref_id: str, create_notification_fn: Callable) -> None:
    """
    create_notification_fn دالة محقونة من NTF (لا استيراد مباشر لخدمة NTF هنا
    إطلاقًا)؛ PUR هي المبادر الوحيد. لا اشتراك لـNTF بأي حدث من PUR بأي حال.
    """
    create_notification_fn(
        target_ref_id=seller_store_ref_id,
        notification_type="pur_seller_reminder",
        context={"purchase_request_id": pr_id},
    )


def send_buyer_reminder(pr_id: str, buyer_user_ref_id: str, create_notification_fn: Callable) -> None:
    create_notification_fn(
        target_ref_id=buyer_user_ref_id,
        notification_type="pur_buyer_reminder",
        context={"purchase_request_id": pr_id},
    )
