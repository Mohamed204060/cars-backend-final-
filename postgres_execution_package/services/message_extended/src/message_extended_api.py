"""
message_extended_api.py — طبقة REST API لتوسعة خدمة التواصل (COM Extended)
المرجع: Orders/Messaging/Notifications Contract Extension؛ CR-007

نطاق هذا الإصدار: تسجيل بيانات مرفق وصفية فقط (اسم/نوع/حجم) — لا رفع ملفات
فعليًا (يستوجب تخزينًا ثنائيًا خارج نطاق هذا المشروع المرجعي بالكامل).

مؤجَّل عمدًا لهذا الـIncrement (موثَّق كـBacklog، لا حذفًا صامتًا):
مؤشر الكتابة (Typing، غير مخزَّن أصلاً بتصميم الكود نفسه)، الحضور/آخر ظهور
(Presence)، كتم/أرشفة المحادثة لكل مستخدم، وتتبع التسليم/القراءة — جميعها
موجودة في message_extended_service.py وتحتاج REST مستقل لاحقًا.
"""

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from session_service import Session
from message_extended_service import AttachmentRejectedError, add_attachment_via_repository

router = APIRouter(prefix="/api/v1", tags=["messaging-extended"])


class AttachmentCreateRequest(BaseModel):
    file_name: str
    mime_type: str
    size_bytes: int


class AttachmentResponse(BaseModel):
    id: str
    message_id: str
    file_name: str
    mime_type: str
    size_bytes: int


def get_message_extended_repository(request: Request):
    return request.app.state.message_extended_repository


@router.post("/messages/{message_id}/attachments", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
def add_attachment(
    message_id: str,
    body: AttachmentCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    message_extended_repo=Depends(get_message_extended_repository),
):
    try:
        attachment = add_attachment_via_repository(
            message_extended_repo, message_id=message_id, file_name=body.file_name,
            mime_type=body.mime_type, size_bytes=body.size_bytes,
        )
    except AttachmentRejectedError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "ATTACHMENT_REJECTED", str(exc))
    return AttachmentResponse(id=attachment.id, message_id=attachment.message_id, file_name=attachment.file_name,
                               mime_type=attachment.mime_type, size_bytes=attachment.size_bytes)
