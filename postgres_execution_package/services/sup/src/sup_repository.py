"""
sup_repository.py — طبقة الوصول للبيانات لخدمة الدعم الفني (SUP)
المرجع: دليل حوكمة التنفيذ v1.7؛ 014_sup.sql
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from sup_service import Reply, Ticket


class SupRepository(ABC):

    @abstractmethod
    def insert_ticket(self, ticket: Ticket) -> Ticket:
        raise NotImplementedError

    @abstractmethod
    def get_ticket_by_id(self, ticket_id: str) -> Optional[Ticket]:
        raise NotImplementedError

    @abstractmethod
    def get_tickets_for_requester(self, requester_ref_id: str) -> List[Ticket]:
        raise NotImplementedError

    @abstractmethod
    def update_ticket(self, ticket: Ticket) -> Ticket:
        raise NotImplementedError

    @abstractmethod
    def insert_reply(self, reply: Reply) -> Reply:
        raise NotImplementedError

    @abstractmethod
    def get_replies_for_ticket(self, ticket_id: str) -> List[Reply]:
        raise NotImplementedError


class PostgresSupRepository(SupRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 014_sup.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_ticket(self, ticket: Ticket) -> Ticket:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO sup.tickets (requester_ref_id, assigned_moderator_ref_id, subject, status, reopen_window_expires_at) "
                "VALUES (%(requester_ref_id)s, %(assigned_moderator_ref_id)s, %(subject)s, %(status)s, %(reopen_window_expires_at)s) "
                "RETURNING id",
                {"requester_ref_id": ticket.requester_ref_id, "assigned_moderator_ref_id": ticket.assigned_moderator_ref_id,
                 "subject": ticket.subject, "status": ticket.status,
                 "reopen_window_expires_at": ticket.reopen_window_expires_at},
            )
            ticket.id = cur.fetchone()["id"]
        return ticket

    def get_ticket_by_id(self, ticket_id: str) -> Optional[Ticket]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, requester_ref_id, assigned_moderator_ref_id, subject, status, reopen_window_expires_at "
                "FROM sup.tickets WHERE id = %(id)s",
                {"id": ticket_id},
            )
            row = cur.fetchone()
        return self._row_to_ticket(row) if row else None

    def get_tickets_for_requester(self, requester_ref_id: str) -> List[Ticket]:
        # يعتمد على idx_tickets_requester
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, requester_ref_id, assigned_moderator_ref_id, subject, status, reopen_window_expires_at "
                "FROM sup.tickets WHERE requester_ref_id = %(requester_ref_id)s",
                {"requester_ref_id": requester_ref_id},
            )
            rows = cur.fetchall()
        return [self._row_to_ticket(r) for r in rows]

    @staticmethod
    def _row_to_ticket(row) -> Ticket:
        return Ticket(id=row["id"], requester_ref_id=row["requester_ref_id"],
                       assigned_moderator_ref_id=row["assigned_moderator_ref_id"], subject=row["subject"],
                       status=row["status"], reopen_window_expires_at=row["reopen_window_expires_at"])

    def update_ticket(self, ticket: Ticket) -> Ticket:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE sup.tickets SET assigned_moderator_ref_id = %(assigned_moderator_ref_id)s, "
                    "status = %(status)s, reopen_window_expires_at = %(reopen_window_expires_at)s, updated_at = now() "
                    "WHERE id = %(id)s",
                    {"assigned_moderator_ref_id": ticket.assigned_moderator_ref_id, "status": ticket.status,
                     "reopen_window_expires_at": ticket.reopen_window_expires_at, "id": ticket.id},
                )
        return ticket

    def insert_reply(self, reply: Reply) -> Reply:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO sup.replies (ticket_id, author_ref_id, body) "
                "VALUES (%(ticket_id)s, %(author_ref_id)s, %(body)s) RETURNING id",
                {"ticket_id": reply.ticket_id, "author_ref_id": reply.author_ref_id, "body": reply.body},
            )
            reply.id = cur.fetchone()["id"]
        return reply

    def get_replies_for_ticket(self, ticket_id: str) -> List[Reply]:
        # يعتمد على idx_replies_ticket_id
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, ticket_id, author_ref_id, body FROM sup.replies WHERE ticket_id = %(ticket_id)s ORDER BY created_at ASC",
                {"ticket_id": ticket_id},
            )
            rows = cur.fetchall()
        return [Reply(id=r["id"], ticket_id=r["ticket_id"], author_ref_id=r["author_ref_id"], body=r["body"]) for r in rows]


class InMemorySupRepository(SupRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._tickets = {}
        self._replies = {}
        self._seq = {"ticket": 1, "reply": 1}

    def insert_ticket(self, ticket: Ticket) -> Ticket:
        ticket.id = f"ticket-{self._seq['ticket']}"
        self._seq["ticket"] += 1
        self._tickets[ticket.id] = ticket
        return ticket

    def get_ticket_by_id(self, ticket_id: str) -> Optional[Ticket]:
        return self._tickets.get(ticket_id)

    def get_tickets_for_requester(self, requester_ref_id: str) -> List[Ticket]:
        return [t for t in self._tickets.values() if t.requester_ref_id == requester_ref_id]

    def update_ticket(self, ticket: Ticket) -> Ticket:
        self._tickets[ticket.id] = ticket
        return ticket

    def insert_reply(self, reply: Reply) -> Reply:
        reply.id = f"reply-{self._seq['reply']}"
        self._seq["reply"] += 1
        self._replies[reply.id] = reply
        return reply

    def get_replies_for_ticket(self, ticket_id: str) -> List[Reply]:
        return [r for r in self._replies.values() if r.ticket_id == ticket_id]
