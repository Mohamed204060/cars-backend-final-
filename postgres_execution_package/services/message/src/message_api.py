"""
message_api.py — طبقة REST API لخدمة التواصل (COM)
المرجع: Orders/Messaging/Notifications Contract Extension؛ REQ-COM-001..010؛ CR-015

CR-015: عُولجت الفجوة الموصوفة سابقًا هنا (لا تتبُّع لأطراف المحادثة) عبر
027_com_conversation_participants.sql + message_service/repository. القراءة
والحذف الآن مقصوران على المشاركين المسجَّلين فعليًا (403 لغير المشارك).
الإرسال يبقى بلا تغيير عمدًا (قرار نطاق موثَّق في message_service.py) —
أي مستخدم بجلسة صالحة يمكنه بدء تواصل على سياق صالح، لكنه يُسجَّل تلقائيًا
كمشارك فور نجاح الإرسال.

resolve_canonical_participant: دالة تركيب على مستوى هذه الطبقة فقط (لا COM
نفسها) تحقن معرفة PUR/STR عبر الـRepositories العامة الموجودة أصلًا
(get_order_repository/get_store_repository)، حفاظًا على عزل COM (SSOT) —
نفس نمط pct_repo.is_part_approved المُستخدَم في order_api.py.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import get_auth_repository_for_role_check
from order_api import get_order_repository
from store_api import get_store_repository
from inventory_item_api import get_inventory_repository
from session_service import Session
from aud_repository import AuditEvent
from message_service import (
    EmptyMessageBodyError,
    InvalidContextTypeError,
    delete_message_for_recipient_via_repository,
    delete_message_for_sender_via_repository,
    list_conversations_for_user_via_repository,
    send_message_via_repository,
)

router = APIRouter(prefix="/api/v1", tags=["messaging"])

# نفس مجموعة rpt_api.SENSITIVE_REPORT_ROLES حرفيًا — مُكرَّرة هنا عمدًا (لا
# مستوردة) لتفادي أي اعتماد إضافي غير ضروري بين ملفات API منفصلة؛ نفس نمط
# SYSTEM_ADMIN_ROLES المكرَّر في auth_api.py لسبب مشابه (تقليل الاقتران).
# محتوى الرسائل بيانات حساسة بنفس تصنيف Member 360° Sensitive تمامًا
# (Gap Sweep v2.2، بند 5/6) — super_admin حصرًا، لا SYSTEM_ADMIN_ROLES العامة.
MESSAGE_CONTENT_ADMIN_ROLES = {"super_admin"}


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
    created_at: Optional[str] = None  # CR-015 (نقطة 3): com.messages.created_at، ISO-8601


class ConversationLastMessagePreview(BaseModel):
    body_preview: str
    sent_at: Optional[str] = None


class ConversationSummaryResponse(BaseModel):
    id: str
    context_type: str
    context_ref_id: str
    # قائمة لا معرّف واحد: محادثة سياق purchase_request قد تضم أكثر من
    # بائع فعليًا (لا تقسيم للمحادثة حسب مقدّم العرض في التصميم الحالي) —
    # راجع 027_com_conversation_participants.sql وCR-015 §4.
    other_participant_user_ref_ids: list[str]
    last_message: Optional[ConversationLastMessagePreview] = None


class ConversationListResponse(BaseModel):
    items: list[ConversationSummaryResponse]
    pagination: dict


def get_message_repository(request: Request):
    return request.app.state.message_repository


def get_aud_repository(request: Request):
    return request.app.state.aud_repository


def _to_response(message) -> MessageResponse:
    return MessageResponse(
        id=message.id, conversation_id=message.conversation_id, sender_user_ref_id=message.sender_user_ref_id,
        body=message.body, is_deleted_by_sender=message.is_deleted_by_sender,
        is_deleted_by_recipient=message.is_deleted_by_recipient,
        created_at=message.created_at.isoformat() if message.created_at else None,
    )


_MESSAGE_PREVIEW_MAX_CHARS = 140


def _resolve_canonical_participant(context_type: str, context_ref_id: str, order_repo, store_repo, inventory_repo) -> Optional[str]:
    """يُستدعى فقط عند إنشاء محادثة جديدة فعليًا؛ يعيد None بأمان عند تعذّر الاشتقاق (لا افتراض)."""
    if context_type == "purchase_request":
        pr = order_repo.get_purchase_request_by_id(context_ref_id)
        return pr.buyer_user_ref_id if pr is not None else None
    if context_type == "inventory_item":
        item = inventory_repo.get_item_by_id(context_ref_id)
        if item is None:
            return None
        store = store_repo.get_store_by_id(item.store_id)
        return store.owner_user_ref_id if store is not None else None
    return None


@router.post("/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def send_message(
    body: MessageSendRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_repo=Depends(get_message_repository),
    order_repo=Depends(get_order_repository),
    store_repo=Depends(get_store_repository),
    inventory_repo=Depends(get_inventory_repository),
):
    def _resolver(context_type, context_ref_id):
        return _resolve_canonical_participant(context_type, context_ref_id, order_repo, store_repo, inventory_repo)

    try:
        message = send_message_via_repository(
            message_repo, context_type=body.context_type, context_ref_id=body.context_ref_id,
            sender_user_ref_id=current_session.user_id, body=body.body,
            resolve_canonical_participant_fn=_resolver,
        )
    except InvalidContextTypeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_CONTEXT_TYPE", str(exc))
    except EmptyMessageBodyError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "EMPTY_MESSAGE_BODY", str(exc))
    return _to_response(message)


def _ensure_participant(correlation_id, message_repo, conversation_id, user_id):
    """CR-015: بوابة Authorization موحَّدة لـlist/delete — 403 لغير المشارك المسجَّل."""
    if not message_repo.is_participant(conversation_id, user_id):
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "لا يجوز الوصول إلى محادثة لست طرفًا مسجَّلًا فيها.")


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_repo=Depends(get_message_repository),
):
    """
    CR-015: مقصور الآن على المشاركين المسجَّلين فعليًا (403 غير ذلك) — يعالج
    الفجوة الموصوفة سابقًا في هذا الملف. ضمن نطاق المشارك المصرَّح له، يُعاد
    فقط ما لم يحذفه هو تحديدًا (نفس منطق الرؤية النسبي السابق، بلا تغيير).
    """
    _ensure_participant(correlation_id, message_repo, conversation_id, current_session.user_id)

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
    sender_user_ref_id بمعرّف الجلسة الحالية. CR-015: مقصور على المشاركين.
    ترتيب التحقق مقصود: NOT_FOUND (404) قبل FORBIDDEN (403) — رسالة غير
    موجودة أصلًا لا معنى لتفويضها؛ نفس ترتيب باقي الـEndpoints في المشروع
    (مثل update_store_status: 404 قبل أي فحص صلاحية)."""
    messages = message_repo.get_messages_for_conversation(conversation_id)
    target = next((m for m in messages if m.id == message_id), None)
    if target is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "MESSAGE_NOT_FOUND", "الرسالة غير موجودة.")

    _ensure_participant(correlation_id, message_repo, conversation_id, current_session.user_id)

    if target.sender_user_ref_id == current_session.user_id:
        updated = delete_message_for_sender_via_repository(message_repo, message_id=message_id, conversation_id=conversation_id)
    else:
        updated = delete_message_for_recipient_via_repository(message_repo, message_id=message_id, conversation_id=conversation_id)
    return _to_response(updated)


@router.get("/conversations", response_model=ConversationListResponse)
def list_my_conversations(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_repo=Depends(get_message_repository),
    page: int = 1,
    page_size: int = 20,
):
    """CR-015 (Endpoint جديد، Gap #6). لا unread_count هنا عمدًا: تتبُّع
    القراءة (com.message_delivery_tracking) موجود بنيويًا لكن غير مفعَّل في
    أي مسار REST حاليًا (لا شيء يكتب read_at فعليًا) — إضافته الآن كانت
    ستُنتج حقلًا مضلِّلًا (كل شيء "غير مقروء" دومًا)، لا بيانات فعلية."""
    items, total = list_conversations_for_user_via_repository(
        message_repo, user_ref_id=current_session.user_id, page=page, page_size=page_size,
    )
    summaries = []
    for conv in items:
        others = [p for p in message_repo.get_participant_ids(conv.id) if p != current_session.user_id]
        last = message_repo.get_last_message_for_conversation(conv.id)
        last_preview = None
        if last is not None:
            preview_text = last.body[:_MESSAGE_PREVIEW_MAX_CHARS]
            last_preview = ConversationLastMessagePreview(
                body_preview=preview_text, sent_at=last.created_at.isoformat() if last.created_at else None,
            )
        summaries.append(ConversationSummaryResponse(
            id=conv.id, context_type=conv.context_type, context_ref_id=conv.context_ref_id,
            other_participant_user_ref_ids=others, last_message=last_preview,
        ))
    return ConversationListResponse(
        items=summaries, pagination={"page": page, "page_size": page_size, "total_items": total},
    )


# ---------------------------------------------------------------------------
# GET /admin/conversations/{conversation_id}/messages — Admin Operational
# Completion: Private-Message Administrative Access (Gap Sweep v2.2، بند 5/6)
#
# المتطلب المعتمَد (Reports Catalog §6): "الوصول إلى محتوى الرسائل يخضع
# لصلاحيات Audit المعتمدة ولا يعرض تلقائيًا لكل مستخدم إداري" — مسار مستقل
# تمامًا عن GET /conversations/{id}/messages العادي:
# - بلا اعتماد على is_deleted_by_sender/is_deleted_by_recipient إطلاقًا (نص
#   الرسالة نفسه لا يُحذَف فعليًا أبدًا — 011_com.sql، حذف نسبي فقط، فـ
#   message_repo.get_messages_for_conversation نفسها تُعيد كل الصفوف دومًا؛
#   الفلترة حسب الحذف تحدث فقط في list_messages العادي أعلاه، لا هنا).
# - super_admin حصرًا (MESSAGE_CONTENT_ADMIN_ROLES) — لا نموذج صلاحية جديد،
#   نفس تصنيف Member 360° Sensitive المعتمَد سابقًا.
# - كل وصول يُسجَّل في aud.events (log_type='administrative') قبل إعادة أي
#   محتوى — النطاق فقط (conversation_id + عدد الرسائل)، لا محتوى أي رسالة
#   إطلاقًا في metadata (ممنوع صراحة في Gap Sweep v2.2، بند 6).
# ---------------------------------------------------------------------------

@router.get("/admin/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
def admin_list_conversation_messages(
    conversation_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_repo=Depends(get_message_repository),
    aud_repo=Depends(get_aud_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in MESSAGE_CONTENT_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "هذه العملية تتطلب صلاحية super_admin تحديدًا (بيانات حساسة).")

    # بلا فلترة حسب is_deleted_by_sender/is_deleted_by_recipient إطلاقًا —
    # هذا بالضبط الفرق الجوهري عن list_messages العادي أعلاه؛ المسار
    # الإداري يرى كل شيء لأن المحتوى نفسه لم يُحذَف فعليًا قط من القاعدة.
    messages = message_repo.get_messages_for_conversation(conversation_id)

    # تسجيل الوصول *قبل* إعادة الاستجابة عمدًا — الوصول نفسه هو الحدث محل
    # التدقيق، بصرف النظر عن نجاح إعادة الاستجابة لاحقًا. لا محتوى رسالة أي
    # كان يدخل metadata — النطاق فقط (معرّف المحادثة + عدد الرسائل)، يكفي
    # للتحقيق لاحقًا بلا كشف المحتوى نفسه داخل سجل التدقيق ذاته.
    aud_repo.insert_event(AuditEvent(
        id=None, log_type="administrative", event_name="admin_message_content_accessed",
        correlation_id=correlation_id, actor_ref_id=current_session.user_id,
        occurred_at_utc=None, before_value=None, after_value=None, reason=None,
        metadata={"conversation_id": conversation_id, "message_count": len(messages)},
    ))

    return [_to_response(m) for m in messages]
