"""
message_extended_service.py — توسعة خدمة التواصل (COM) للإصدار الأول
المرجع: CR-007 (v1.4، معتمَد)؛ SRS الحزمة D v1.6

مبدأ تصميمي: وحدة جديدة مستقلة تمامًا عن message_service.py (لا تعديل عليه
إطلاقًا، بنفس نمط pur_distribution_service.py مقابل order_service.py)؛
الكيانات الجديدة تُشير للرسالة/المحادثة الأساسية بمعرّف مرجعي فقط، لا
توسيعًا مباشرًا لحقول Message/Conversation القائمة.

استقلالية المجال: لا استيراد مباشر لأي خدمة أعمال أخرى (PUR/STR/IAM/TRM)؛
أي تكامل مستقبلي (كحظر مستخدم أو تكامل TRM للإبلاغ) عبر دالة محقونة فقط.
لا حذف فعلي لأي كيان من كيانات هذا الملف؛ الأرشفة/الحالة فقط.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


ALLOWED_ATTACHMENT_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp",
    "application/pdf",
}
FORBIDDEN_EXTENSIONS = {".exe", ".bat", ".sh", ".js", ".cmd", ".msi", ".com", ".scr"}
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 ميغابايت لكل مرفق


# ---------------------------------------------------------------------------
# مؤشر الكتابة (Typing Indicator) — إشارة لحظية عابرة، بلا سجل تاريخي
# ---------------------------------------------------------------------------

def build_typing_signal(conversation_id: str, user_ref_id: str, is_typing: bool) -> dict:
    """إشارة لحظية فقط للنقل الفوري (Real-Time)؛ لا تُخزَّن كسجل دائم."""
    return {"event": "typing", "conversation_id": conversation_id,
            "user_ref_id": user_ref_id, "is_typing": is_typing}


# ---------------------------------------------------------------------------
# آخر ظهور وحالة الاتصال (Last Seen / Online Presence)
# ---------------------------------------------------------------------------

@dataclass
class UserPresence:
    user_ref_id: str  # SSOT: مرجع IAM فقط
    is_online: bool = False
    last_seen_at: Optional[datetime] = None


def mark_user_online(presence: UserPresence) -> UserPresence:
    presence.is_online = True
    return presence


def mark_user_offline(presence: UserPresence, occurred_at: datetime) -> UserPresence:
    presence.is_online = False
    presence.last_seen_at = occurred_at
    return presence


# ---------------------------------------------------------------------------
# حالة التسليم والقراءة (Delivered / Read Status) — سجل مرتبط لا حقل مباشر
# ---------------------------------------------------------------------------

@dataclass
class MessageDeliveryTracking:
    message_id: str  # SSOT: إشارة مرجعية فقط لرسالة قائمة في message_service.py
    sent_at: datetime
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None


class InvalidDeliveryTrackingTransitionError(Exception):
    """لا يجوز تسجيل قراءة قبل تسليم، أو تسليم مكرَّر."""


def mark_delivered(tracking: MessageDeliveryTracking, occurred_at: datetime) -> MessageDeliveryTracking:
    if tracking.delivered_at is not None:
        raise InvalidDeliveryTrackingTransitionError("الرسالة مُسجَّلة كمُسلَّمة بالفعل.")
    tracking.delivered_at = occurred_at
    return tracking


def mark_read(tracking: MessageDeliveryTracking, occurred_at: datetime) -> MessageDeliveryTracking:
    if tracking.delivered_at is None:
        raise InvalidDeliveryTrackingTransitionError("لا يجوز تسجيل القراءة قبل التسليم.")
    if tracking.read_at is not None:
        raise InvalidDeliveryTrackingTransitionError("الرسالة مُسجَّلة كمقروءة بالفعل.")
    tracking.read_at = occurred_at
    return tracking


# ---------------------------------------------------------------------------
# الرد وإعادة التوجيه (Reply / Forward) — سجلات ربط مرجعية
# ---------------------------------------------------------------------------

@dataclass
class MessageThreadLink:
    message_id: str          # الرسالة الجديدة (الرد)
    reply_to_message_id: str  # الرسالة الأصل المردود عليها


class SelfReplyError(Exception):
    """لا يجوز أن ترد الرسالة على نفسها."""


def create_reply_link(message_id: str, reply_to_message_id: str) -> MessageThreadLink:
    if message_id == reply_to_message_id:
        raise SelfReplyError("لا يجوز أن ترد الرسالة على نفسها.")
    return MessageThreadLink(message_id=message_id, reply_to_message_id=reply_to_message_id)


@dataclass
class ForwardRecord:
    original_message_id: str
    forwarded_message_id: str
    forwarded_to_conversation_id: str


def create_forward_record(original_message_id: str, forwarded_message_id: str,
                           forwarded_to_conversation_id: str) -> ForwardRecord:
    return ForwardRecord(original_message_id=original_message_id, forwarded_message_id=forwarded_message_id,
                          forwarded_to_conversation_id=forwarded_to_conversation_id)


# ---------------------------------------------------------------------------
# المرفقات (Attachments) — REQ-COM-016: حد أدنى أمني إلزامي
# ---------------------------------------------------------------------------

class AttachmentRejectedError(Exception):
    """رفض مرفق لعدم استيفاء الحد الأدنى الأمني (نوع/حجم/امتداد)."""


@dataclass
class Attachment:
    id: str
    message_id: str
    file_name: str
    mime_type: str
    size_bytes: int


def validate_and_create_attachment(message_id: str, file_name: str, mime_type: str, size_bytes: int) -> Attachment:
    lowered_name = file_name.lower()
    for forbidden_ext in FORBIDDEN_EXTENSIONS:
        if lowered_name.endswith(forbidden_ext):
            raise AttachmentRejectedError(
                f"نوع الملف '{forbidden_ext}' مرفوض أمنيًا (تنفيذي أو خطِر)."
            )
    if mime_type not in ALLOWED_ATTACHMENT_MIME_TYPES:
        raise AttachmentRejectedError(
            f"نوع المحتوى (MIME Type) '{mime_type}' غير مسموح به."
        )
    if size_bytes <= 0:
        raise AttachmentRejectedError("حجم الملف يجب أن يكون أكبر من صفر.")
    if size_bytes > MAX_ATTACHMENT_SIZE_BYTES:
        raise AttachmentRejectedError(
            f"حجم الملف يتجاوز الحد الأقصى المسموح ({MAX_ATTACHMENT_SIZE_BYTES} بايت)."
        )
    return Attachment(id="", message_id=message_id, file_name=file_name,
                       mime_type=mime_type, size_bytes=size_bytes)


# ---------------------------------------------------------------------------
# البحث (Conversation / Message Search) — منطق بحث نصي بسيط، الفهرسة اهتمام بنيوي لاحق
# ---------------------------------------------------------------------------

def search_messages_by_text(messages: list, query: str) -> list:
    """messages: قائمة كائنات تحمل خاصية body نصية (رسائل من message_service.py)."""
    if not query or not query.strip():
        return []
    normalized_query = query.strip().lower()
    return [m for m in messages if normalized_query in m.body.lower()]


# ---------------------------------------------------------------------------
# إعدادات المحادثة الخاصة بالمستخدم (Mute / Archive) — لكل مستخدم على حدة
# ---------------------------------------------------------------------------

@dataclass
class ConversationUserSettings:
    conversation_id: str
    user_ref_id: str  # SSOT
    is_muted: bool = False
    is_archived: bool = False


def mute_conversation(settings: ConversationUserSettings) -> ConversationUserSettings:
    settings.is_muted = True
    return settings


def unmute_conversation(settings: ConversationUserSettings) -> ConversationUserSettings:
    settings.is_muted = False
    return settings


def archive_conversation_for_user(settings: ConversationUserSettings) -> ConversationUserSettings:
    """أرشفة خاصة بهذا المستخدم فقط؛ لا تؤثر على رؤية الطرف الآخر للمحادثة، ولا حذف فعلي."""
    settings.is_archived = True
    return settings


def unarchive_conversation_for_user(settings: ConversationUserSettings) -> ConversationUserSettings:
    settings.is_archived = False
    return settings


# ---------------------------------------------------------------------------
# الإبلاغ (Report Conversation/Message) — تكامل مع مفهوم TRM القائم لا آلية جديدة
# ---------------------------------------------------------------------------

def build_message_or_conversation_report_event(target_type: str, target_ref_id: str,
                                                reported_by_user_ref_id: str, reason: str) -> dict:
    if target_type not in {"message", "conversation"}:
        raise ValueError(f"نوع هدف بلاغ غير معروف: {target_type}")
    if not reason or not reason.strip():
        raise ValueError("سبب البلاغ يجب ألا يكون فارغًا.")
    return {"report_target_type": target_type, "report_target_ref_id": target_ref_id,
            "reported_by_user_ref_id": reported_by_user_ref_id, "reason": reason}


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4/1.7)
# ---------------------------------------------------------------------------

def go_online_via_repository(repository, user_ref_id: str) -> UserPresence:
    presence = repository.get_presence(user_ref_id) or UserPresence(user_ref_id=user_ref_id)
    mark_user_online(presence)
    return repository.upsert_presence(presence)


def go_offline_via_repository(repository, user_ref_id: str, occurred_at: datetime) -> UserPresence:
    presence = repository.get_presence(user_ref_id) or UserPresence(user_ref_id=user_ref_id)
    mark_user_offline(presence, occurred_at)
    return repository.upsert_presence(presence)


def mark_delivered_via_repository(repository, message_id: str, sent_at: datetime, occurred_at: datetime) -> MessageDeliveryTracking:
    tracking = repository.get_delivery_tracking(message_id)
    if tracking is None:
        tracking = MessageDeliveryTracking(message_id=message_id, sent_at=sent_at)
        repository.insert_delivery_tracking(tracking)
    mark_delivered(tracking, occurred_at)
    return repository.update_delivery_tracking(tracking)


def mark_read_via_repository(repository, message_id: str, occurred_at: datetime) -> MessageDeliveryTracking:
    tracking = repository.get_delivery_tracking(message_id)
    if tracking is None:
        raise ValueError(f"لا يوجد تتبّع تسليم لرسالة بالمعرّف: {message_id}")
    mark_read(tracking, occurred_at)
    return repository.update_delivery_tracking(tracking)


def add_attachment_via_repository(repository, message_id: str, file_name: str, mime_type: str, size_bytes: int) -> Attachment:
    attachment = validate_and_create_attachment(message_id, file_name, mime_type, size_bytes)
    return repository.insert_attachment(attachment)


def mute_conversation_via_repository(repository, conversation_id: str, user_ref_id: str) -> ConversationUserSettings:
    settings = repository.get_conversation_user_settings(conversation_id, user_ref_id) \
        or ConversationUserSettings(conversation_id=conversation_id, user_ref_id=user_ref_id)
    mute_conversation(settings)
    return repository.upsert_conversation_user_settings(settings)


def archive_conversation_for_user_via_repository(repository, conversation_id: str, user_ref_id: str) -> ConversationUserSettings:
    settings = repository.get_conversation_user_settings(conversation_id, user_ref_id) \
        or ConversationUserSettings(conversation_id=conversation_id, user_ref_id=user_ref_id)
    archive_conversation_for_user(settings)
    return repository.upsert_conversation_user_settings(settings)
