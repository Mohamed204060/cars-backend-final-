"""
ntf_api.py — طبقة REST API لمركز الإشعارات (Notification Center)
المرجع: Orders/Messaging/Notifications Contract Extension

النطاق: مركز الإشعارات الموجَّه للمستخدم النهائي فقط (عرض/تعليم كمقروء/
أرشفة)، مطابقةً للنطاق الموثَّق أصلًا داخل ntf_service.py نفسه. إدارة
الحملات (Campaign/Delivery/Template/ChannelProvider) مؤجَّلة عمدًا؛ لا
غلاف *_via_repository لها أصلًا، وتستوجب تصميم REST إداري منفصل أوسع.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from session_service import Session
from ntf_service import (
    NotificationNotFoundError,
    archive_notification_via_repository,
    list_notifications_via_repository,
    mark_notification_read_via_repository,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    id: str
    recipient_id: str
    is_read: bool
    is_archived_by_user: bool


def get_ntf_repository(request: Request):
    return request.app.state.ntf_repository


def _to_response(entry) -> NotificationResponse:
    return NotificationResponse(id=entry.id, recipient_id=entry.recipient_id,
                                 is_read=entry.is_read, is_archived_by_user=entry.is_archived_by_user)


@router.get("/", response_model=list[NotificationResponse])
def list_my_notifications(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ntf_repo=Depends(get_ntf_repository),
):
    entries = list_notifications_via_repository(ntf_repo, user_ref_id=current_session.user_id)
    return [_to_response(e) for e in entries]


@router.post("/{entry_id}/read", response_model=NotificationResponse)
def mark_read(
    entry_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ntf_repo=Depends(get_ntf_repository),
):
    try:
        entry = mark_notification_read_via_repository(ntf_repo, entry_id=entry_id, user_ref_id=current_session.user_id)
    except NotificationNotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "NOTIFICATION_NOT_FOUND", str(exc))
    return _to_response(entry)


@router.post("/{entry_id}/archive", response_model=NotificationResponse)
def archive_notification(
    entry_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ntf_repo=Depends(get_ntf_repository),
):
    try:
        entry = archive_notification_via_repository(ntf_repo, entry_id=entry_id, user_ref_id=current_session.user_id)
    except NotificationNotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "NOTIFICATION_NOT_FOUND", str(exc))
    return _to_response(entry)
