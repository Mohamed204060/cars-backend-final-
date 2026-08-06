"""
message_service.py — منطق خدمة التواصل (COM)
المرجع: REQ-COM-001..010

SSOT: context_ref_id يشير لطلب شراء (PUR) أو عنصر مخزون (STR) بمعرّف مرجعي
فقط؛ sender_user_ref_id يشير لخدمة IAM بمعرّف مرجعي فقط. لا نسخ بيانات.

مبدأ عدم الحذف الفعلي (دليل الحوكمة 6.6): الرسائل بيانات مجال أعمال
(سجل تواصل تجاري)؛ الحذف نسبي لكل طرف (is_deleted_by_sender/recipient)
لا حذف فعلي مطلقًا — يختلف هذا عن حذف كيان كامل: كل طرف يرى نسخته
الخاصة من "الحذف" دون التأثير على الطرف الآخر أو على السجل نفسه.
"""

from dataclasses import dataclass
from typing import Optional, List


VALID_CONTEXT_TYPES = {"purchase_request", "inventory_item"}


@dataclass
class Conversation:
    id: str
    context_type: str
    context_ref_id: str  # SSOT: إشارة مرجعية فقط لخدمة PUR أو STR بحسب النوع


@dataclass
class Message:
    id: str
    conversation_id: str
    sender_user_ref_id: str  # SSOT: إشارة مرجعية فقط لخدمة IAM
    body: str
    is_deleted_by_sender: bool = False
    is_deleted_by_recipient: bool = False


class InvalidContextTypeError(Exception):
    """REQ-COM-002: سياق محادثة غير معروف."""


class EmptyMessageBodyError(Exception):
    """لا يجوز إرسال رسالة بمحتوى فارغ."""


# ---------------------------------------------------------------------------
# REQ-COM-001, 002: إنشاء محادثة مرتبطة بسياق محدَّد
# ---------------------------------------------------------------------------

def create_conversation(context_type: str, context_ref_id: str) -> Conversation:
    if context_type not in VALID_CONTEXT_TYPES:
        raise InvalidContextTypeError(f"نوع سياق محادثة غير معروف: {context_type}")
    return Conversation(id="", context_type=context_type, context_ref_id=context_ref_id)


# ---------------------------------------------------------------------------
# REQ-COM-001: إرسال رسالة
# ---------------------------------------------------------------------------

def send_message(conversation: Conversation, sender_user_ref_id: str, body: str) -> Message:
    if not body or not body.strip():
        raise EmptyMessageBodyError("لا يجوز إرسال رسالة بمحتوى فارغ.")
    return Message(id="", conversation_id=conversation.id,
                    sender_user_ref_id=sender_user_ref_id, body=body)


# ---------------------------------------------------------------------------
# REQ-COM-007: حذف منطقي نسبي لكل طرف (لا حالة عامة للرسالة)
# ---------------------------------------------------------------------------

def delete_message_for_sender(message: Message) -> Message:
    """
    ملاحظة تسمية مهمة: هذه ليست دالة \"حذف فعلي\" رغم اسمها؛ هي تبديل علم
    رؤية نسبي لطرف واحد فقط (Soft, Per-Viewer)، لا حذف السجل نفسه ولا حتى
    تأثير على رؤية الطرف الآخر له. لا تُزال الرسالة من أي مخزن بيانات.
    """
    message.is_deleted_by_sender = True
    return message


def delete_message_for_recipient(message: Message) -> Message:
    """نفس ملاحظة delete_message_for_sender أعلاه؛ حذف نسبي للمستلِم فقط."""
    message.is_deleted_by_recipient = True
    return message


def is_message_visible_to(message: Message, viewer_role: str) -> bool:
    """viewer_role: 'sender' أو 'recipient'؛ يحدِّد ما إذا كانت الرسالة ظاهرة لهذا الطرف تحديدًا."""
    if viewer_role == "sender":
        return not message.is_deleted_by_sender
    if viewer_role == "recipient":
        return not message.is_deleted_by_recipient
    raise ValueError(f"دور مشاهِد غير معروف: {viewer_role}")


# ---------------------------------------------------------------------------
# REQ-COM-010: المحادثة تبقى مفتوحة بعد إتمام طلب الشراء المرتبط بها
# ---------------------------------------------------------------------------
# ملاحظة تصميمية مهمة: لا توجد في هذا الملف أي دالة تُغلِق محادثة تبعًا لحالة
# طلب الشراء المرتبط بها؛ عمدًا، لتحقيق REQ-COM-010 حرفيًا: تجميد بيانات
# الصفقة لا يعني إغلاق قناة التواصل. لا حقل "status" على Conversation إطلاقًا.


# ---------------------------------------------------------------------------
# بناء وصف حدث (لا كتابة فعلية؛ AUD هو المرجع الوحيد للتاريخ الزمني، SSOT)
# ---------------------------------------------------------------------------

def build_message_audit_event(action: str, actor_ref_id: str, conversation_id: str):
    allowed_actions = {"message_sent", "message_deleted_by_sender", "message_deleted_by_recipient"}
    if action not in allowed_actions:
        raise ValueError(f"نوع حدث غير معروف: {action}")
    return {"log_type": "general", "event_name": action, "actor_ref_id": actor_ref_id,
            "metadata": {"conversation_id": conversation_id}}


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4/1.7)
# ---------------------------------------------------------------------------

def get_or_create_conversation_via_repository(repository, context_type: str, context_ref_id: str) -> Conversation:
    """يعيد استخدام محادثة قائمة لنفس السياق إن وُجدت، بدلاً من إنشاء واحدة مكرَّرة."""
    existing = repository.get_conversation_by_context(context_type, context_ref_id)
    if existing is not None:
        return existing
    return repository.insert_conversation(create_conversation(context_type, context_ref_id))


def send_message_via_repository(repository, context_type: str, context_ref_id: str,
                                 sender_user_ref_id: str, body: str) -> Message:
    conversation = get_or_create_conversation_via_repository(repository, context_type, context_ref_id)
    message = send_message(conversation, sender_user_ref_id, body)
    return repository.insert_message(message)


def delete_message_for_sender_via_repository(repository, message_id: str, conversation_id: str) -> Message:
    messages = repository.get_messages_for_conversation(conversation_id)
    target = next((m for m in messages if m.id == message_id), None)
    if target is None:
        raise ValueError(f"لا توجد رسالة بالمعرّف: {message_id}")
    delete_message_for_sender(target)
    return repository.update_message(target)


def delete_message_for_recipient_via_repository(repository, message_id: str, conversation_id: str) -> Message:
    messages = repository.get_messages_for_conversation(conversation_id)
    target = next((m for m in messages if m.id == message_id), None)
    if target is None:
        raise ValueError(f"لا توجد رسالة بالمعرّف: {message_id}")
    delete_message_for_recipient(target)
    return repository.update_message(target)
