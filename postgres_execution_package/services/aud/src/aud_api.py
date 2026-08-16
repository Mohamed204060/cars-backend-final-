"""
aud_api.py — طبقة REST API لخدمة AUD (سجل التدقيق)
المرجع: REQ-AUD-001..012

قرار تصميمي مقصود (طلب صريح من مالك المشروع — "لا تسمح بتعديل/حذف سجلات
Audit الاعتيادية من وظائف الإدارة"): لا يوجد أي POST/PUT/DELETE هنا إطلاقًا.
مسار الكتابة الوحيد هو استدعاء برمجي داخلي من كود كل Domain (عبر
record_audit_event_via_repository)، وليس عبر أي Endpoint عام — حتى الإداري
المخوَّل لا يستطيع "إدخال" سجل تدقيق يدويًا من الواجهة. هذا يمنع بابًا كاملاً
للتلاعب البشري، فوق منع قاعدة البيانات (REVOKE UPDATE/DELETE) المطبَّق أصلًا.

القراءة (GET /audit/events) للإداري (SYSTEM_ADMIN_ROLES) حصرًا — نفس فحص
الصلاحية المعتمَد حرفيًا في pct_api.py/cmp_api.py/vct_api.py (لا نمط جديد).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from aud_service import (
    InvalidLogTypeError,
    InvalidRefIdError,
    list_audit_events_via_repository,
)

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    id: str
    log_type: str
    event_name: str
    correlation_id: Optional[str] = None
    actor_ref_id: Optional[str] = None
    occurred_at_utc: str
    before_value: Optional[dict] = None
    after_value: Optional[dict] = None
    reason: Optional[str] = None
    metadata: Optional[dict] = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    pagination: PaginationMeta


def get_aud_repository(request: Request):
    return request.app.state.aud_repository


def _to_response(e) -> AuditEventResponse:
    return AuditEventResponse(
        id=e.id, log_type=e.log_type, event_name=e.event_name,
        correlation_id=e.correlation_id, actor_ref_id=e.actor_ref_id,
        occurred_at_utc=e.occurred_at_utc.isoformat() if hasattr(e.occurred_at_utc, "isoformat") else str(e.occurred_at_utc),
        before_value=e.before_value, after_value=e.after_value,
        reason=e.reason, metadata=e.metadata,
    )


@router.get("/events", response_model=AuditEventListResponse)
def list_audit_events(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    aud_repo=Depends(get_aud_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    log_type: Optional[str] = None,
    event_name: Optional[str] = None,
    actor_ref_id: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    # REQ-AUD-004/009: مدير النظام حصريًا — سجل التدقيق بيانات إدارية حساسة
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")

    try:
        items, total = list_audit_events_via_repository(
            aud_repo, log_type=log_type, event_name=event_name, actor_ref_id=actor_ref_id,
            date_from=date_from, date_to=date_to, page=page, page_size=page_size,
        )
    except InvalidLogTypeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_LOG_TYPE", "قيمة log_type غير معروفة.")
    except InvalidRefIdError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_REF_ID", "قيمة actor_ref_id ليست UUID صالحًا.")

    return AuditEventListResponse(
        items=[_to_response(e) for e in items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )
