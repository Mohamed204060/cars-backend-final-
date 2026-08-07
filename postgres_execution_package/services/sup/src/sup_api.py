"""
sup_api.py — طبقة REST API لخدمة الدعم الفني (SUP)
المرجع: REQ-SUP-001..006

الصلاحيات: أي مستخدم مسجَّل يُنشئ طلبه ويرد عليه ويطلب إعادة فتحه (REQ-SUP-001/005/006).
الإسناد/الحل — مشرف الدعم الفني (support_moderator) أو مدير النظام حصريًا (REQ-SUP-003).
عرض/الرد يقتصر على مقدِّم الطلب أو المشرف المُسنَد له أو أي مشرف/مدير.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from sup_service import (
    EmptyReplyBodyError,
    InvalidTicketStatusError,
    TicketReopenWindowExpiredError,
    add_reply_via_repository,
    assign_ticket_via_repository,
    close_ticket_via_repository,
    create_ticket_via_repository,
    reopen_ticket_via_repository,
    resolve_ticket_via_repository,
)

router = APIRouter(prefix="/api/v1/support/tickets", tags=["support"])

MODERATOR_ROLES = SYSTEM_ADMIN_ROLES | {"support_moderator"}


class TicketCreateRequest(BaseModel):
    subject: str


class TicketResponse(BaseModel):
    id: str
    requester_ref_id: str
    assigned_moderator_ref_id: Optional[str] = None
    subject: str
    status: str
    reopen_window_expires_at: Optional[datetime] = None


class AssignRequest(BaseModel):
    moderator_ref_id: str


class ReplyCreateRequest(BaseModel):
    body: str


class ReplyResponse(BaseModel):
    id: str
    ticket_id: str
    author_ref_id: str
    body: str


def get_sup_repository(request: Request):
    return request.app.state.sup_repository


def get_auth_repository_for_moderator_check(request: Request):
    return request.app.state.auth_repository


def _to_response(ticket) -> TicketResponse:
    return TicketResponse(id=ticket.id, requester_ref_id=ticket.requester_ref_id,
                           assigned_moderator_ref_id=ticket.assigned_moderator_ref_id,
                           subject=ticket.subject, status=ticket.status,
                           reopen_window_expires_at=ticket.reopen_window_expires_at)


def _ensure_participant_or_moderator(correlation_id, auth_repo, ticket, user_id):
    if ticket.requester_ref_id == user_id or ticket.assigned_moderator_ref_id == user_id:
        return
    role = auth_repo.get_user_role(user_id)
    if role not in MODERATOR_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "هذه العملية مقصورة على مقدِّم الطلب أو المشرف المسؤول عنه.")


@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    body: TicketCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
):
    try:
        ticket = create_ticket_via_repository(sup_repo, requester_ref_id=current_session.user_id, subject=body.subject)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_SUBJECT", str(exc))
    return _to_response(ticket)


@router.get("/mine", response_model=list[TicketResponse])
def list_my_tickets(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
):
    return [_to_response(t) for t in sup_repo.get_tickets_for_requester(current_session.user_id)]


@router.get("/{ticket_id}", response_model=TicketResponse)
def get_ticket(
    ticket_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
    auth_repo=Depends(get_auth_repository_for_moderator_check),
):
    ticket = sup_repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TICKET_NOT_FOUND", "طلب الدعم غير موجود.")
    _ensure_participant_or_moderator(correlation_id, auth_repo, ticket, current_session.user_id)
    return _to_response(ticket)


@router.post("/{ticket_id}/assign", response_model=TicketResponse)
def assign_ticket(
    ticket_id: str,
    body: AssignRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
    auth_repo=Depends(get_auth_repository_for_moderator_check),
):
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in MODERATOR_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "الإسناد مقصور على مشرف الدعم الفني أو مدير النظام.")
    try:
        ticket = assign_ticket_via_repository(sup_repo, ticket_id=ticket_id, moderator_ref_id=body.moderator_ref_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TICKET_NOT_FOUND", str(exc))
    except InvalidTicketStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _to_response(ticket)


@router.post("/{ticket_id}/resolve", response_model=TicketResponse)
def resolve_ticket(
    ticket_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
    auth_repo=Depends(get_auth_repository_for_moderator_check),
):
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in MODERATOR_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "الحل مقصور على مشرف الدعم الفني أو مدير النظام.")
    try:
        ticket = resolve_ticket_via_repository(sup_repo, ticket_id=ticket_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TICKET_NOT_FOUND", str(exc))
    except InvalidTicketStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _to_response(ticket)


@router.post("/{ticket_id}/close", response_model=TicketResponse)
def close_ticket(
    ticket_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
    auth_repo=Depends(get_auth_repository_for_moderator_check),
):
    ticket = sup_repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TICKET_NOT_FOUND", "طلب الدعم غير موجود.")
    _ensure_participant_or_moderator(correlation_id, auth_repo, ticket, current_session.user_id)
    try:
        ticket = close_ticket_via_repository(sup_repo, ticket_id=ticket_id, now=datetime.now(timezone.utc))
    except InvalidTicketStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _to_response(ticket)


@router.post("/{ticket_id}/reopen", response_model=TicketResponse)
def reopen_ticket(
    ticket_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
):
    ticket = sup_repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TICKET_NOT_FOUND", "طلب الدعم غير موجود.")
    # REQ-SUP-006: مقدِّم الطلب حصرًا
    if ticket.requester_ref_id != current_session.user_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "إعادة الفتح مقصورة على مقدِّم الطلب.")
    try:
        ticket = reopen_ticket_via_repository(sup_repo, ticket_id=ticket_id, now=datetime.now(timezone.utc))
    except InvalidTicketStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    except TicketReopenWindowExpiredError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "REOPEN_WINDOW_EXPIRED", str(exc))
    return _to_response(ticket)


@router.post("/{ticket_id}/replies", response_model=ReplyResponse, status_code=status.HTTP_201_CREATED)
def add_reply(
    ticket_id: str,
    body: ReplyCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
    auth_repo=Depends(get_auth_repository_for_moderator_check),
):
    ticket = sup_repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TICKET_NOT_FOUND", "طلب الدعم غير موجود.")
    _ensure_participant_or_moderator(correlation_id, auth_repo, ticket, current_session.user_id)
    try:
        reply = add_reply_via_repository(sup_repo, ticket_id=ticket_id, author_ref_id=current_session.user_id, body=body.body)
    except EmptyReplyBodyError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "EMPTY_REPLY_BODY", str(exc))
    return ReplyResponse(id=reply.id, ticket_id=reply.ticket_id, author_ref_id=reply.author_ref_id, body=reply.body)


@router.get("/{ticket_id}/replies", response_model=list[ReplyResponse])
def list_replies(
    ticket_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sup_repo=Depends(get_sup_repository),
    auth_repo=Depends(get_auth_repository_for_moderator_check),
):
    ticket = sup_repo.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TICKET_NOT_FOUND", "طلب الدعم غير موجود.")
    _ensure_participant_or_moderator(correlation_id, auth_repo, ticket, current_session.user_id)
    replies = sup_repo.get_replies_for_ticket(ticket_id)
    return [ReplyResponse(id=r.id, ticket_id=r.ticket_id, author_ref_id=r.author_ref_id, body=r.body) for r in replies]
