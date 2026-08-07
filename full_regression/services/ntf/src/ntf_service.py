"""
ntf_service.py — منطق خدمة الإشعارات والبث الجماعي (NTF)
المرجع: SRS مجال NTF v1.1 (المرجع الرسمي الوحيد)؛ ADR-034

SSOT: user_ref_id يشير لخدمة IAM بمعرّف مرجعي فقط في كل مكان. لا استدعاء
مباشر لخدمة COM بأي حال (BR-NTF-004). لا حذف فعلي لأي كيان (BR-NTF-001) —
الأرشفة والحذف النسبي فقط.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ---------------------------------------------------------------------------
# الثوابت وآلات الحالة
# ---------------------------------------------------------------------------

CAMPAIGN_STATUSES = {"draft", "scheduled", "running", "completed", "cancelled", "paused", "archived"}
CAMPAIGN_ALLOWED_TRANSITIONS = {
    "draft": {"scheduled", "running", "cancelled"},
    "scheduled": {"running", "cancelled"},
    "running": {"completed", "paused", "cancelled", "failed"},
    "paused": {"running", "cancelled"},
    "completed": {"archived"},
    "cancelled": {"archived"},
    "failed": {"archived"},
    "archived": set(),
}

DELIVERY_STATUSES = {"pending", "queued", "sent", "delivered", "read", "failed", "cancelled"}
DELIVERY_ALLOWED_TRANSITIONS = {
    "pending": {"queued", "cancelled", "failed"},  # فشل مبكر ممكن (كمزوِّد قناة غير معروف) قبل حتى الدخول للطابور
    "queued": {"sent", "failed", "cancelled"},
    "sent": {"delivered", "failed"},
    "delivered": {"read"},
    "read": set(),
    "failed": set(),
    "cancelled": set(),
}

CHANNEL_HEALTH_STATUSES = {"healthy", "degraded", "offline"}
FAILURE_REASONS = {"provider_failure", "network_failure", "invalid_recipient",
                   "rate_limit", "expired_message", "permission_denied", "unknown_error"}


# ---------------------------------------------------------------------------
# الكيانات (Class-Level، وفق SAD)
# ---------------------------------------------------------------------------

@dataclass
class Campaign:
    id: str
    created_by_user_ref_id: str  # SSOT: مرجع IAM فقط
    title: str
    body: str
    audience_type: str  # static | dynamic
    status: str = "draft"
    priority: str = "normal"  # critical | high | normal | low
    campaign_version: int = 1
    template_version_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


@dataclass
class Delivery:
    id: str
    campaign_id: str
    campaign_version_snapshot: int
    correlation_id: str  # يُمرَّر عبر كل الطبقات (Logs/AUD/Queue/Workers/Providers/RPT)
    execution_status: str = "running"
    total_recipients: int = 0


@dataclass
class Recipient:
    id: str
    delivery_id: str
    user_ref_id: str  # SSOT: مرجع IAM فقط
    channel_provider_code: str
    status: str = "pending"
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    failure_reason_code: Optional[str] = None
    retry_count: int = 0


@dataclass
class Template:
    id: str
    code: str
    status: str = "active"
    current_version_number: int = 1


@dataclass
class TemplateVersion:
    id: str
    template_id: str
    version_number: int
    title: str
    body: str


@dataclass
class ChannelProviderInfo:
    code: str
    display_name: str
    health_status: str = "healthy"
    is_enabled: bool = True


@dataclass
class NotificationPreference:
    id: str
    user_ref_id: str  # SSOT
    channel_provider_code: str
    notification_type: str
    is_enabled: bool = True


@dataclass
class NotificationCenterEntry:
    id: str
    recipient_id: str
    user_ref_id: str  # SSOT
    is_read: bool = False
    is_archived_by_user: bool = False
    is_deleted_by_user: bool = False


# ---------------------------------------------------------------------------
# استثناءات منطق الأعمال
# ---------------------------------------------------------------------------

class InvalidCampaignStatusError(Exception):
    """انتقال حالة غير مسموح به لحملة."""


class InvalidDeliveryStatusError(Exception):
    """انتقال حالة غير مسموح به لعملية تنفيذ."""


class DuplicateRecipientError(Exception):
    """REQ-NTF-012: منع تكرار سجل مستلِم لنفس المستخدم لنفس عملية التنفيذ."""


class PreferenceBlockedError(Exception):
    """REQ-NTF-035: المستخدم عطَّل هذا النوع/القناة، ولا توجد أولوية Critical لتجاوز ذلك."""


class SchedulingValidationError(Exception):
    """REQ-NTF-031: فشل أحد شروط التحقق من سلامة الجدولة."""


class TemplateArchivedImmutableError(Exception):
    """BR-NTF-006: لا تعديل على محتوى إصدار قالب موجود؛ التعديل ينتج إصدارًا جديدًا فقط."""


# ---------------------------------------------------------------------------
# Correlation ID (مطلوب صراحة من المالك: يُمرَّر عبر كل الطبقات)
# ---------------------------------------------------------------------------

def generate_correlation_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# REQ-NTF-001, 020: إنشاء حملة (نموذج البيانات المنفصل Campaign/Delivery/Recipient)
# ---------------------------------------------------------------------------

def create_campaign(created_by_user_ref_id: str, title: str, body: str, audience_type: str,
                     priority: str = "normal") -> Campaign:
    if audience_type not in {"static", "dynamic"}:
        raise ValueError(f"نوع جمهور غير معروف: {audience_type}")
    if priority not in {"critical", "high", "normal", "low"}:
        raise ValueError(f"أولوية غير معروفة: {priority}")
    return Campaign(id="", created_by_user_ref_id=created_by_user_ref_id,
                     title=title, body=body, audience_type=audience_type, priority=priority)


def transition_campaign_status(campaign: Campaign, new_status: str) -> Campaign:
    if new_status not in CAMPAIGN_STATUSES:
        raise ValueError(f"حالة حملة غير معروفة: {new_status}")
    allowed = CAMPAIGN_ALLOWED_TRANSITIONS.get(campaign.status, set())
    if new_status not in allowed:
        raise InvalidCampaignStatusError(
            f"الانتقال من '{campaign.status}' إلى '{new_status}' غير مسموح به."
        )
    campaign.status = new_status
    return campaign


# ---------------------------------------------------------------------------
# REQ-NTF-031: تحقق سلامة الجدولة
# ---------------------------------------------------------------------------

def validate_scheduling(campaign: Campaign, scheduled_at: datetime, current_time: datetime,
                         has_recipients: bool, has_channel: bool, has_valid_template: bool) -> None:
    if scheduled_at < current_time:
        raise SchedulingValidationError("لا يجوز جدولة حملة بتاريخ سابق.")
    if campaign.expires_at is not None and campaign.expires_at <= current_time:
        raise SchedulingValidationError("لا يجوز جدولة حملة منتهية الصلاحية أصلاً.")
    if not has_recipients:
        raise SchedulingValidationError("لا يجوز جدولة حملة بلا مستلمين.")
    if not has_channel:
        raise SchedulingValidationError("لا يجوز جدولة حملة بلا قناة إرسال محدَّدة.")
    if not has_valid_template:
        raise SchedulingValidationError("لا يجوز جدولة حملة بقالب غير صالح.")


# ---------------------------------------------------------------------------
# REQ-NTF-020, 021, 025: إنشاء Delivery وسجلات Recipient (منع التكرار + Idempotency)
# ---------------------------------------------------------------------------

def create_delivery_for_campaign(campaign: Campaign) -> Delivery:
    correlation_id = generate_correlation_id()
    return Delivery(id="", campaign_id=campaign.id, campaign_version_snapshot=campaign.campaign_version,
                     correlation_id=correlation_id)


def add_recipient(delivery: Delivery, existing_recipients_for_delivery: List[Recipient],
                   user_ref_id: str, channel_provider_code: str) -> Recipient:
    """REQ-NTF-012: سجل واحد فقط لكل مستخدم لكل Delivery (لا لكل معيار استهداف متطابق)."""
    for existing in existing_recipients_for_delivery:
        if existing.user_ref_id == user_ref_id:
            raise DuplicateRecipientError(
                f"يوجد بالفعل سجل مستلِم لهذا المستخدم ضمن عملية التنفيذ '{delivery.id}'."
            )
    return Recipient(id="", delivery_id=delivery.id, user_ref_id=user_ref_id,
                      channel_provider_code=channel_provider_code)


def transition_recipient_status(recipient: Recipient, new_status: str,
                                 occurred_at: datetime, failure_reason_code: Optional[str] = None) -> Recipient:
    if new_status not in DELIVERY_STATUSES:
        raise ValueError(f"حالة مستلِم غير معروفة: {new_status}")
    allowed = DELIVERY_ALLOWED_TRANSITIONS.get(recipient.status, set())
    if new_status not in allowed:
        raise InvalidDeliveryStatusError(
            f"الانتقال من '{recipient.status}' إلى '{new_status}' غير مسموح به."
        )
    recipient.status = new_status
    if new_status == "sent":
        recipient.sent_at = occurred_at
    elif new_status == "delivered":
        recipient.delivered_at = occurred_at
    elif new_status == "read":
        recipient.read_at = occurred_at
    elif new_status == "failed":
        if failure_reason_code is not None and failure_reason_code not in FAILURE_REASONS:
            raise ValueError(f"سبب فشل غير معروف: {failure_reason_code}")
        recipient.failure_reason_code = failure_reason_code
    return recipient


# ---------------------------------------------------------------------------
# REQ-NTF-034, 035: تفضيلات الإشعارات وإنفاذها (مع تجاوز Critical)
# ---------------------------------------------------------------------------

def is_send_allowed_by_preferences(
    preferences_for_user: List[NotificationPreference],
    channel_provider_code: str, notification_type: str, campaign_priority: str,
) -> bool:
    if campaign_priority == "critical":
        return True  # REQ-NTF-035: الإشعارات الحرجة تتجاوز أي تعطيل
    for pref in preferences_for_user:
        if pref.channel_provider_code == channel_provider_code and pref.notification_type == notification_type:
            return pref.is_enabled
    return True  # لا تفضيل محفوظ = مسموح افتراضيًا


def ensure_send_allowed(preferences_for_user: List[NotificationPreference],
                         channel_provider_code: str, notification_type: str, campaign_priority: str) -> None:
    if not is_send_allowed_by_preferences(preferences_for_user, channel_provider_code,
                                           notification_type, campaign_priority):
        raise PreferenceBlockedError(
            f"المستخدم عطَّل إشعارات '{notification_type}' عبر قناة '{channel_provider_code}'."
        )


# ---------------------------------------------------------------------------
# BR-NTF-006: القوالب Append-Only (لا تعديل بأثر رجعي)
# ---------------------------------------------------------------------------

def create_template(code: str, initial_title: str, initial_body: str):
    template = Template(id="", code=code)
    version = TemplateVersion(id="", template_id="", version_number=1, title=initial_title, body=initial_body)
    return template, version


def create_new_template_version(template: Template, new_title: str, new_body: str) -> TemplateVersion:
    """أي تعديل على قالب نشط يُنشئ إصدارًا جديدًا فقط؛ لا تعديل على إصدار قائم مطلقًا."""
    if template.status == "archived":
        raise TemplateArchivedImmutableError("لا يجوز إنشاء إصدار جديد لقالب مؤرشَف.")
    new_version_number = template.current_version_number + 1
    template.current_version_number = new_version_number
    return TemplateVersion(id="", template_id=template.id, version_number=new_version_number,
                            title=new_title, body=new_body)


# ---------------------------------------------------------------------------
# مركز الإشعارات (REQ-NTF-036): حذف نسبي للمستخدم فقط
# ---------------------------------------------------------------------------

def mark_notification_read(entry: NotificationCenterEntry) -> NotificationCenterEntry:
    entry.is_read = True
    return entry


def archive_notification_for_user(entry: NotificationCenterEntry) -> NotificationCenterEntry:
    entry.is_archived_by_user = True
    return entry


def delete_notification_for_user(entry: NotificationCenterEntry) -> NotificationCenterEntry:
    """حذف نسبي خاص بالمستخدم فقط؛ لا حذف فعلي للسجل نفسه (BR-NTF-001)."""
    entry.is_deleted_by_user = True
    return entry


# ---------------------------------------------------------------------------
# صحة مزوِّد القناة (ChannelProvider State Machine)
# ---------------------------------------------------------------------------

def update_channel_health(provider: ChannelProviderInfo, new_status: str) -> ChannelProviderInfo:
    if new_status not in CHANNEL_HEALTH_STATUSES:
        raise ValueError(f"حالة صحية غير معروفة: {new_status}")
    provider.health_status = new_status
    return provider


def build_administrative_audit_event(action: str, actor_ref_id: str, campaign_id: str,
                                      correlation_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    allowed_actions = {"campaign_created", "campaign_scheduled", "campaign_cancelled", "campaign_resumed",
                       "audience_changed", "channel_changed", "template_edited", "priority_changed"}
    if action not in allowed_actions:
        raise ValueError(f"نوع حدث غير معروف: {action}")
    return {"log_type": "administrative", "event_name": action, "actor_ref_id": actor_ref_id,
            "metadata": {"campaign_id": campaign_id, "correlation_id": correlation_id, "reason": reason}}
