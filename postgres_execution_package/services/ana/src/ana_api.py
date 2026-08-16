"""
ana_api.py — طبقة REST API لـAnalytics Event Foundation
المرجع: Reporting/Analytics Catalog v1.0 §32

POST /analytics/events: تسجيل حدث تحليلي. جلسة اختيارية (get_optional_session)
عمدًا — كثير من الأحداث (تصفح/بحث) تحدث قبل تسجيل الدخول؛ actor_ref_id
يُشتَق من الجلسة إن وُجدت، بلا فرضها. هذا Endpoint عام (Ownership/Scoping غير
منطبق هنا أصلًا — لا مورد مملوك)، وليس عملية إدارية، فلا فحص دور.

GET /analytics/events: للإداري (SYSTEM_ADMIN_ROLES) فقط — نفس نمط aud_api.py
حرفيًا، لأغراض المراجعة/التصحيح الآن (لوحات Dashboard المجمَّعة الفعلية في
Slice 2 اللاحقة).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session, get_optional_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from ana_service import (
    InvalidEventTypeError,
    InvalidRefIdError,
    MetadataTooLargeError,
    list_analytics_events_via_repository,
    record_analytics_event_via_repository,
)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


class AnalyticsEventCreateRequest(BaseModel):
    event_type: str
    context_type: Optional[str] = None
    context_ref_id: Optional[str] = None
    metadata: Optional[dict] = None


class AnalyticsEventResponse(BaseModel):
    id: str
    event_type: str
    occurred_at_utc: str
    actor_ref_id: Optional[str] = None
    session_ref_id: Optional[str] = None
    context_type: Optional[str] = None
    context_ref_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Optional[dict] = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class AnalyticsEventListResponse(BaseModel):
    items: list[AnalyticsEventResponse]
    pagination: PaginationMeta


def get_ana_repository(request: Request):
    return request.app.state.ana_repository


def _to_response(e) -> AnalyticsEventResponse:
    return AnalyticsEventResponse(
        id=e.id, event_type=e.event_type,
        occurred_at_utc=e.occurred_at_utc.isoformat() if hasattr(e.occurred_at_utc, "isoformat") else str(e.occurred_at_utc),
        actor_ref_id=e.actor_ref_id, session_ref_id=e.session_ref_id,
        context_type=e.context_type, context_ref_id=e.context_ref_id,
        correlation_id=e.correlation_id, metadata=e.metadata,
    )


@router.post("/events", response_model=AnalyticsEventResponse, status_code=status.HTTP_201_CREATED)
def record_event(
    body: AnalyticsEventCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    optional_session: Optional[Session] = Depends(get_optional_session),
    ana_repo=Depends(get_ana_repository),
):
    try:
        event = record_analytics_event_via_repository(
            ana_repo, event_type=body.event_type,
            actor_ref_id=optional_session.user_id if optional_session else None,
            context_type=body.context_type, context_ref_id=body.context_ref_id,
            correlation_id=correlation_id, metadata=body.metadata,
        )
    except InvalidEventTypeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_EVENT_TYPE", "قيمة event_type غير معروفة/غير معتمَدة.")
    except InvalidRefIdError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_REF_ID", "قيمة context_ref_id ليست UUID صالحًا.")
    except MetadataTooLargeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "METADATA_TOO_LARGE", str(exc))

    return _to_response(event)


@router.get("/events", response_model=AnalyticsEventListResponse)
def list_events(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ana_repo=Depends(get_ana_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    event_type: Optional[str] = None,
    context_type: Optional[str] = None,
    context_ref_id: Optional[str] = None,
    actor_ref_id: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")

    try:
        items, total = list_analytics_events_via_repository(
            ana_repo, event_type=event_type, context_type=context_type, context_ref_id=context_ref_id,
            actor_ref_id=actor_ref_id, date_from=date_from, date_to=date_to, page=page, page_size=page_size,
        )
    except InvalidEventTypeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_EVENT_TYPE", "قيمة event_type غير معروفة/غير معتمَدة.")
    except InvalidRefIdError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_REF_ID", "قيمة context_ref_id ليست UUID صالحًا.")

    return AnalyticsEventListResponse(
        items=[_to_response(e) for e in items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=total),
    )
