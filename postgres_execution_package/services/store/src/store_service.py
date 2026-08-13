"""
store_service.py — منطق خدمة المتاجر (STR)
المرجع: REQ-STR-001..008 (المتجر ودورة حياته وآلية طلب التصحيح)؛
        مبدأ SSOT الجديد المعتمد صراحة: هذه الخدمة المرجع الوحيد لبيانات
        المتجر؛ لا تُخزَّن أي بيانات هوية/مستخدم مباشرة هنا سوى معرّف مرجعي
        (owner_user_ref_id)، اتساقًا مع أن خدمة المصادقة (IAM) هي المرجع
        الوحيد لبيانات الهوية.

هذا الملف يُطبِّق منطق الأعمال المستقل عن قاعدة البيانات، بنفس نمط
search_service.py وauth_service.py تمامًا.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


VALID_STATUSES = {"creating", "active", "suspended", "archived"}

# REQ-STR-004: انتقالات الحالة المسموحة فقط
ALLOWED_TRANSITIONS = {
    "creating": {"active"},
    "active": {"suspended", "archived"},
    "suspended": {"active", "archived"},
    "archived": set(),  # حالة نهائية؛ لا عودة منها
}


@dataclass
class Store:
    id: str
    owner_user_ref_id: str  # SSOT: إشارة مرجعية فقط لخدمة الهوية، لا نسخ لبياناتها
    status: str = "creating"
    country_ref_id: Optional[str] = None
    city_ref_id: Optional[str] = None
    correction_deadline: Optional[datetime] = None  # REQ-STR-007
    has_pending_correction: bool = False


class InvalidStatusTransitionError(Exception):
    """REQ-STR-004: انتقال حالة غير مسموح به."""


class UnauthorizedOwnershipTransferError(Exception):
    """REQ-STR-006: نقل الملكية مقصور على مدير النظام فقط."""


class CorrectionWindowStillOpenError(Exception):
    """REQ-STR-008: التعديل المباشر من المشرف غير مسموح قبل انقضاء المهلة."""


# ---------------------------------------------------------------------------
# REQ-STR-001: إنشاء المتجر التلقائي عند تسجيل بائع جديد
# ---------------------------------------------------------------------------

def create_store(owner_user_ref_id: str, country_ref_id: Optional[str] = None,
                  city_ref_id: Optional[str] = None) -> Store:
    """
    SSOT: يُستقبَل owner_user_ref_id كمعرّف جاهز من خدمة الهوية؛ هذه الدالة لا
    تتحقق من صحته ولا تخزّن أي تفاصيل عن صاحبه — تلك مسؤولية خدمة IAM حصرًا.
    """
    return Store(
        id="",  # يُسنَد فعليًا عبر Repository
        owner_user_ref_id=owner_user_ref_id,
        status="active",  # الإنشاء التلقائي ينتج متجرًا قابلاً للاستخدام مباشرة
        country_ref_id=country_ref_id,
        city_ref_id=city_ref_id,
    )


# ---------------------------------------------------------------------------
# REQ-STR-004: دورة حياة حالة المتجر
# ---------------------------------------------------------------------------

def transition_store_status(store: Store, new_status: str) -> Store:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"حالة غير معروفة: {new_status}")

    allowed = ALLOWED_TRANSITIONS.get(store.status, set())
    if new_status not in allowed:
        raise InvalidStatusTransitionError(
            f"الانتقال من '{store.status}' إلى '{new_status}' غير مسموح به."
        )

    store.status = new_status
    return store


# ---------------------------------------------------------------------------
# REQ-STR-006: نقل الملكية مقصور على مدير النظام
# ---------------------------------------------------------------------------

def transfer_ownership(store: Store, new_owner_user_ref_id: str, actor_role: str) -> Store:
    """
    SSOT: new_owner_user_ref_id مجرَّد معرّف مرجعي؛ لا تتحقق هذه الدالة من
    وجوده الفعلي في خدمة الهوية — تلك مسؤولية طبقة التنسيق (Repository/API)
    التي تتكامل مع خدمة IAM قبل استدعاء هذه الدالة.
    """
    if actor_role != "admin":
        raise UnauthorizedOwnershipTransferError(
            "نقل ملكية المتجر مقصور على مدير النظام فقط."
        )
    store.owner_user_ref_id = new_owner_user_ref_id
    return store


# ---------------------------------------------------------------------------
# REQ-STR-007, 008: آلية طلب التصحيح للمتجر (إشراف متدرِّج)
# ---------------------------------------------------------------------------

def send_correction_request(store: Store, deadline: datetime) -> Store:
    store.has_pending_correction = True
    store.correction_deadline = deadline
    return store


def resolve_correction_by_seller(store: Store) -> Store:
    """REQ-STR-007 (المسار 4أ): البائع يعدِّل بنفسه، فيُغلَق طلب التصحيح فورًا."""
    store.has_pending_correction = False
    store.correction_deadline = None
    return store


def moderator_direct_edit(store: Store, actor_role: str, current_time: datetime) -> Store:
    """REQ-STR-008 (المسار 4ب): تعديل المشرف المباشر مسموح فقط بعد انقضاء المهلة."""
    if actor_role != "moderator":
        raise PermissionError("التعديل المباشر على بيانات متجر مقصور على المشرفين.")
    if not store.has_pending_correction or store.correction_deadline is None:
        raise ValueError("لا يوجد طلب تصحيح معلَّق على هذا المتجر.")
    if current_time < store.correction_deadline:
        raise CorrectionWindowStillOpenError(
            "لم تنقضِ مهلة التصحيح بعد؛ لا يجوز التعديل المباشر قبل انتهائها."
        )
    store.has_pending_correction = False
    store.correction_deadline = None
    return store


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4)
# ---------------------------------------------------------------------------

def create_store_via_repository(repository, owner_user_ref_id: str,
                                 country_ref_id: Optional[str] = None,
                                 city_ref_id: Optional[str] = None) -> Store:
    store = create_store(owner_user_ref_id, country_ref_id, city_ref_id)
    return repository.insert_store(store)


def transition_store_status_via_repository(repository, store_id: str, new_status: str) -> Store:
    store = repository.get_store_by_id(store_id)
    if store is None:
        raise ValueError(f"لا يوجد متجر بالمعرّف: {store_id}")
    transition_store_status(store, new_status)  # يرمي استثناءً إن كان الانتقال غير مسموح
    return repository.update_store(store)


def transfer_ownership_via_repository(repository, store_id: str, new_owner_user_ref_id: str, actor_role: str) -> Store:
    store = repository.get_store_by_id(store_id)
    if store is None:
        raise ValueError(f"لا يوجد متجر بالمعرّف: {store_id}")
    transfer_ownership(store, new_owner_user_ref_id, actor_role)  # يرمي استثناءً إن لم يكن actor_role == admin
    return repository.update_store(store)


# ---------------------------------------------------------------------------
# REQ-AUD-009، 010 (يمتد من CR-005): بناء وصف حدث نشاط إداري
# ---------------------------------------------------------------------------

def build_administrative_audit_event(action: str, actor_ref_id: str, store_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    allowed_actions = {"store_suspended", "store_reactivated", "store_archived",
                       "ownership_transferred", "correction_request_sent", "correction_direct_edit"}
    if action not in allowed_actions:
        raise ValueError(f"نوع حدث غير معروف: {action}")
    return {
        "log_type": "administrative",
        "event_name": action,
        "actor_ref_id": actor_ref_id,
        "metadata": {"store_id": store_id, "reason": reason},
    }
