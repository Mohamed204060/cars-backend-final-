"""
message_api.py — طبقة REST API لخدمة التواصل (COM)
المرجع: Orders/Messaging/Notifications Contract Extension؛ REQ-COM-001..010

ملاحظة نطاق مُقِرّ بها صراحة (Backlog): لا تتبُّع صريح لأطراف المحادثة
(Participants) في المستودع الحالي (Conversation لا تحمل إلا context_type/
context_ref_id)؛ لذلك أي مستخدم بجلسة صالحة يمكنه حاليًا الإرسال والعرض ضمن
أي سياق يعرف مُعرِّفه. إنفاذ عضوية الطرفين الفعليَّين (اشتقاقًا من مالك
الطلب/العنصر مقابل الطرف الآخر) يستوجب تصميمًا إضافيًا؛ يُترَك لـIncrement
لاحق، ولا يُفترَض هنا بصمت.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from session_service import Session
from message_service import (
    EmptyMessageBodyError,
    InvalidContextTypeError,
    delete_message_for_recipient_via_repository,
    delete_message_for_sender_via_repository,
    send_message_via_repository,
)

router = APIRouter(prefix="/api/v1", tags=["messaging"])


class MessageSendRequest(BaseModel):
    context_type: str
    context_ref_id: str
    body: str


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_user_ref_id: str
    body: str
    is_deleted_by_sender: bool
    is_deleted_by_recipient: bool


def get_message_repository(request: Request):
    return request.app.state.message_repository


def _to_response(message) -> MessageResponse:
    return MessageResponse(
        id=message.id, conversation_id=message.conversation_id, sender_user_ref_id=message.sender_user_ref_id,
        body=message.body, is_deleted_by_sender=message.is_deleted_by_sender,
        is_deleted_by_recipient=message.is_deleted_by_recipient,
    )


@router.post("/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def send_message(
    body: MessageSendRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_repo=Depends(get_message_repository),
):
    try:
        message = send_message_via_repository(
            message_repo, context_type=body.context_type, context_ref_id=body.context_ref_id,
            sender_user_ref_id=current_session.user_id, body=body.body,
        )
    except InvalidContextTypeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_CONTEXT_TYPE", str(exc))
    except EmptyMessageBodyError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "EMPTY_MESSAGE_BODY", str(exc))
    return _to_response(message)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_repo=Depends(get_message_repository),
):
    """
    يُعيد فقط الرسائل الظاهرة لصاحب الجلسة الحالية (لا المحذوفة من طرفه هو
    تحديدًا)، بحسب دوره الفعلي: المُرسِل يرى ما لم يحذفه هو، وأي طرف آخر
    (متلقٍّ افتراضًا هنا، بانتظار إنفاذ عضوية دقيق لاحقًا) يرى ما لم يُحذَف
    من جهة المتلقّي.
    """
    messages = message_repo.get_messages_for_conversation(conversation_id)
    visible = []
    for m in messages:
        is_sender = m.sender_user_ref_id == current_session.user_id
        if is_sender and not m.is_deleted_by_sender:
            visible.append(m)
        elif not is_sender and not m.is_deleted_by_recipient:
            visible.append(m)
    return [_to_response(m) for m in visible]


@router.delete("/conversations/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
def delete_message(
    conversation_id: str,
    message_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_repo=Depends(get_message_repository),
):
    """REQ-COM-007: حذف نسبي فقط، لا فعلي؛ يُحدَّد الطرف تلقائيًا بمقارنة
    sender_user_ref_id بمعرّف الجلسة الحالية."""
    messages = message_repo.get_messages_for_conversation(conversation_id)
    target = next((m for m in messages if m.id == message_id), None)
    if target is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "MESSAGE_NOT_FOUND", "الرسالة غير موجودة.")

    if target.sender_user_ref_id == current_session.user_id:
        updated = delete_message_for_sender_via_repository(message_repo, message_id=message_id, conversation_id=conversation_id)
    else:
        updated = delete_message_for_recipient_via_repository(message_repo, message_id=message_id, conversation_id=conversation_id)
    return _to_response(updated)
