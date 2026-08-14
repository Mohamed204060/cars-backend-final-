"""
sup_service.py — منطق خدمة الدعم الفني (SUP)
المرجع: REQ-SUP-001..006 (يشمل 002-A)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional


TICKET_STATUSES = {"open", "in_progress", "resolved", "closed"}
TICKET_ALLOWED_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"resolved", "closed"},
    "resolved": {"closed", "in_progress"},  # إعادة فتح تُعامَل كإعادة انتقال لـin_progress (REQ-SUP-006)
    "closed": {"in_progress"},  # إعادة فتح خلال المهلة فقط (REQ-SUP-006)
}

DEFAULT_REOPEN_WINDOW_HOURS = 72  # REQ-SUP-006: مهلة قابلة للضبط؛ قيمة افتراضية معقولة


@dataclass
class Ticket:
    id: str
    requester_ref_id: str
    subject: str
    assigned_moderator_ref_id: Optional[str] = None
    status: str = "open"
    reopen_window_expires_at: Optional[datetime] = None


@dataclass
class Reply:
    id: str
    ticket_id: str
    author_ref_id: str
    body: str


class InvalidTicketStatusError(Exception):
    """REQ-SUP-002: انتقال حالة غير مسموح به لطلب دعم."""


class TicketReopenWindowExpiredError(Exception):
    """REQ-SUP-006: انقضت مهلة إعادة الفتح؛ يلزم إنشاء طلب جديد."""


class EmptyReplyBodyError(Exception):
    """محتوى الرد يجب ألا يكون فارغًا."""


def create_ticket(requester_ref_id: str, subject: str) -> Ticket:
    if not subject or not subject.strip():
        raise ValueError("موضوع طلب الدعم يجب ألا يكون فارغًا.")
    return Ticket(id="", requester_ref_id=requester_ref_id, subject=subject)


def transition_ticket_status(ticket: Ticket, new_status: str) -> Ticket:
    if new_status not in TICKET_STATUSES:
        raise ValueError(f"حالة طلب دعم غير معروفة: {new_status}")
    allowed = TICKET_ALLOWED_TRANSITIONS.get(ticket.status, set())
    if new_status not in allowed:
        raise InvalidTicketStatusError(f"الانتقال من '{ticket.status}' إلى '{new_status}' غير مسموح به.")
    ticket.status = new_status
    return ticket


def assign_ticket(ticket: Ticket, moderator_ref_id: str) -> Ticket:
    """REQ-SUP-003: الإسناد ينقل الطلب لحالة 'قيد المعالجة' تلقائيًا."""
    ticket.assigned_moderator_ref_id = moderator_ref_id
    if ticket.status == "open":
        transition_ticket_status(ticket, "in_progress")
    return ticket


def resolve_ticket(ticket: Ticket) -> Ticket:
    return transition_ticket_status(ticket, "resolved")


def close_ticket(ticket: Ticket, now: datetime, reopen_window_hours: int = DEFAULT_REOPEN_WINDOW_HOURS) -> Ticket:
    """REQ-SUP-006: عند الإغلاق تُضبَط مهلة إعادة الفتح."""
    transition_ticket_status(ticket, "closed")
    ticket.reopen_window_expires_at = now + timedelta(hours=reopen_window_hours)
    return ticket


def reopen_ticket(ticket: Ticket, now: datetime) -> Ticket:
    """REQ-SUP-006: إعادة الفتح مسموحة فقط ضمن المهلة، ومن حالة closed فقط."""
    if ticket.status != "closed":
        raise InvalidTicketStatusError("إعادة الفتح مسموحة فقط لطلب مغلَق.")
    if ticket.reopen_window_expires_at is None or now >= ticket.reopen_window_expires_at:
        raise TicketReopenWindowExpiredError("انقضت مهلة إعادة فتح هذا الطلب؛ يلزم إنشاء طلب جديد.")
    transition_ticket_status(ticket, "in_progress")
    ticket.reopen_window_expires_at = None
    return ticket


def add_reply(ticket_id: str, author_ref_id: str, body: str) -> Reply:
    """REQ-SUP-005: تبادل ردود متعددة ضمن الطلب نفسه."""
    if not body or not body.strip():
        raise EmptyReplyBodyError("محتوى الرد يجب ألا يكون فارغًا.")
    return Reply(id="", ticket_id=ticket_id, author_ref_id=author_ref_id, body=body)


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository
# ---------------------------------------------------------------------------

def create_ticket_via_repository(repository, requester_ref_id: str, subject: str) -> Ticket:
    ticket = create_ticket(requester_ref_id, subject)
    return repository.insert_ticket(ticket)


def assign_ticket_via_repository(repository, ticket_id: str, moderator_ref_id: str) -> Ticket:
    ticket = repository.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise ValueError(f"لا يوجد طلب دعم بالمعرّف: {ticket_id}")
    assign_ticket(ticket, moderator_ref_id)
    return repository.update_ticket(ticket)


def resolve_ticket_via_repository(repository, ticket_id: str) -> Ticket:
    ticket = repository.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise ValueError(f"لا يوجد طلب دعم بالمعرّف: {ticket_id}")
    resolve_ticket(ticket)
    return repository.update_ticket(ticket)


def close_ticket_via_repository(repository, ticket_id: str, now: datetime) -> Ticket:
    ticket = repository.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise ValueError(f"لا يوجد طلب دعم بالمعرّف: {ticket_id}")
    close_ticket(ticket, now)
    return repository.update_ticket(ticket)


def reopen_ticket_via_repository(repository, ticket_id: str, now: datetime) -> Ticket:
    ticket = repository.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise ValueError(f"لا يوجد طلب دعم بالمعرّف: {ticket_id}")
    reopen_ticket(ticket, now)
    return repository.update_ticket(ticket)


def add_reply_via_repository(repository, ticket_id: str, author_ref_id: str, body: str) -> Reply:
    ticket = repository.get_ticket_by_id(ticket_id)
    if ticket is None:
        raise ValueError(f"لا يوجد طلب دعم بالمعرّف: {ticket_id}")
    reply = add_reply(ticket_id, author_ref_id, body)
    return repository.insert_reply(reply)
