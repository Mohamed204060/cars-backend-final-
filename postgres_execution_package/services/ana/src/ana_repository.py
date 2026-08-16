"""
ana_repository.py — طبقة الوصول للبيانات لـAnalytics Event Foundation
المرجع: CarsMaint Reporting, Analytics, Intelligence & Regulatory Reporting
        Catalog v1.0 §32؛ 033_ana_events.sql (Batch 3A Slice 1)

خفيفة وموجَّهة للتقارير حصرًا (قرار حاكم صريح من مالك المشروع): لا Event
Sourcing، لا إعادة تصميم لأي Domain حالي. هذا الملف يوفّر فقط تسجيل/قراءة
أحداث تحليلية مجمَّعة لاحقًا في Dashboards (Slice 2+) — ربط الاستدعاء الفعلي
من order/search/inventory/إلخ مؤجَّل عمدًا لدفعة لاحقة.

Append-Only بنفس مبدأ aud.events: لا abstractmethod للتعديل أو الحذف.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class AnalyticsEvent:
    id: Optional[str]
    event_type: str
    occurred_at_utc: Optional[datetime]
    actor_ref_id: Optional[str]
    session_ref_id: Optional[str]
    context_type: Optional[str]
    context_ref_id: Optional[str]
    correlation_id: Optional[str]
    metadata: Optional[dict]


class AnaRepository(ABC):
    """العقد الوحيد الذي يعتمد عليه ana_service.py. Append-Only بالتصميم."""

    @abstractmethod
    def insert_event(self, event: AnalyticsEvent) -> AnalyticsEvent:
        raise NotImplementedError

    @abstractmethod
    def list_events(
        self, event_type: Optional[str], context_type: Optional[str], context_ref_id: Optional[str],
        actor_ref_id: Optional[str], date_from: Optional[datetime], date_to: Optional[datetime],
        page: int, page_size: int,
    ) -> "tuple[list[AnalyticsEvent], int]":
        raise NotImplementedError


class PostgresAnaRepository(AnaRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط ana.events (033_ana_events.sql)."""

    def __init__(self, connection):
        self._connection = connection

    @property
    def connection(self):
        return self._connection

    def insert_event(self, event: AnalyticsEvent) -> AnalyticsEvent:
        import json as _json
        query = """
            INSERT INTO ana.events
                (event_type, actor_ref_id, session_ref_id, context_type, context_ref_id, correlation_id, metadata)
            VALUES
                (%(event_type)s, %(actor_ref_id)s, %(session_ref_id)s, %(context_type)s,
                 %(context_ref_id)s, %(correlation_id)s, %(metadata)s)
            RETURNING id, occurred_at_utc
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {
                "event_type": event.event_type, "actor_ref_id": event.actor_ref_id,
                "session_ref_id": event.session_ref_id, "context_type": event.context_type,
                "context_ref_id": event.context_ref_id, "correlation_id": event.correlation_id,
                "metadata": _json.dumps(event.metadata) if event.metadata is not None else None,
            })
            row = cur.fetchone()
            event.id = row["id"]
            event.occurred_at_utc = row["occurred_at_utc"]
        return event

    def list_events(self, event_type, context_type, context_ref_id, actor_ref_id, date_from, date_to, page, page_size):
        # يعتمد على idx_ana_events_type_time / idx_ana_events_context / idx_ana_events_actor حسب الفلاتر
        offset = (page - 1) * page_size
        filters = []
        params: dict[str, Any] = {"limit": page_size, "offset": offset}
        if event_type is not None:
            filters.append("event_type = %(event_type)s")
            params["event_type"] = event_type
        if context_type is not None:
            filters.append("context_type = %(context_type)s")
            params["context_type"] = context_type
        if context_ref_id is not None:
            filters.append("context_ref_id = %(context_ref_id)s")
            params["context_ref_id"] = context_ref_id
        if actor_ref_id is not None:
            filters.append("actor_ref_id = %(actor_ref_id)s")
            params["actor_ref_id"] = actor_ref_id
        if date_from is not None:
            filters.append("occurred_at_utc >= %(date_from)s")
            params["date_from"] = date_from
        if date_to is not None:
            filters.append("occurred_at_utc <= %(date_to)s")
            params["date_to"] = date_to
        where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM ana.events {where_clause}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT id, event_type, occurred_at_utc, actor_ref_id, session_ref_id, "
                f"context_type, context_ref_id, correlation_id, metadata FROM ana.events {where_clause} "
                f"ORDER BY occurred_at_utc DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        items = [
            AnalyticsEvent(
                id=r["id"], event_type=r["event_type"], occurred_at_utc=r["occurred_at_utc"],
                actor_ref_id=r["actor_ref_id"], session_ref_id=r["session_ref_id"],
                context_type=r["context_type"], context_ref_id=r["context_ref_id"],
                correlation_id=r["correlation_id"], metadata=r["metadata"],
            )
            for r in rows
        ]
        return items, total


class InMemoryAnaRepository(AnaRepository):
    """للاختبارات فقط. Append-Only بنفس مبدأ النسخة الحية."""

    def __init__(self):
        self._events: list[AnalyticsEvent] = []
        self._next_id = 1

    def insert_event(self, event: AnalyticsEvent) -> AnalyticsEvent:
        event.id = f"ana-{self._next_id}"
        self._next_id += 1
        event.occurred_at_utc = event.occurred_at_utc or datetime.utcnow()
        self._events.append(event)
        return event

    def list_events(self, event_type, context_type, context_ref_id, actor_ref_id, date_from, date_to, page, page_size):
        items = list(reversed(self._events))
        if event_type is not None:
            items = [e for e in items if e.event_type == event_type]
        if context_type is not None:
            items = [e for e in items if e.context_type == context_type]
        if context_ref_id is not None:
            items = [e for e in items if e.context_ref_id == context_ref_id]
        if actor_ref_id is not None:
            items = [e for e in items if e.actor_ref_id == actor_ref_id]
        if date_from is not None:
            items = [e for e in items if e.occurred_at_utc >= date_from]
        if date_to is not None:
            items = [e for e in items if e.occurred_at_utc <= date_to]
        total = len(items)
        start = (page - 1) * page_size
        return items[start:start + page_size], total
