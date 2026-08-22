"""
aud_repository.py — طبقة الوصول للبيانات لخدمة AUD (سجل التدقيق)
المرجع: DD الحزمة 1 (قسم AUD)؛ REQ-AUD-001..012؛ 004_aud.sql (الجدول
موجود ومغلَق فعلًا منذ الحزمة 1 — لا تعديل عليه، ولا Migration جديدة هنا).

Batch 3A Slice 1 (Foundation): هذه أول طبقة Repository/Service/API فعلية
فوق aud.events. الجدول نفسه كان موجودًا (Append-Only، REVOKE UPDATE/DELETE
FROM PUBLIC) لكن بلا أي كود يكتب/يقرأ منه — auth_service.py وstore_service.py
يبنيان قواميس أحداث جاهزة (build_security_audit_event/
build_administrative_audit_event) "لتمريرها لخدمة AUD الفعلية"، وهذه هي تلك
الخدمة. نفس بنية الحقول حرفيًا: log_type/event_name/actor_ref_id/metadata.

مبدأ عدم القابلية للتلاعب (طلب صريح من مالك المشروع): هذا العقد لا يحتوي،
ولن يحتوي، على أي دالة update_event أو delete_event — الحذف/التعديل ممنوعان
هيكليًا من طبقة الكود نفسها، فوق منع قاعدة البيانات (REVOKE) الموجود أصلًا.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class AuditEvent:
    id: Optional[str]
    log_type: str
    event_name: str
    correlation_id: Optional[str]
    actor_ref_id: Optional[str]
    occurred_at_utc: Optional[datetime]
    before_value: Optional[dict]
    after_value: Optional[dict]
    reason: Optional[str]
    metadata: Optional[dict]


class AudRepository(ABC):
    """العقد الوحيد الذي يعتمد عليه aud_service.py. Append-Only بالتصميم —
    لا abstractmethod للتعديل أو الحذف إطلاقًا، عمدًا."""

    @abstractmethod
    def insert_event(self, event: AuditEvent) -> AuditEvent:
        raise NotImplementedError

    @abstractmethod
    def list_events(
        self, log_type: Optional[str], event_name: Optional[str],
        actor_ref_id: Optional[str], date_from: Optional[datetime], date_to: Optional[datetime],
        page: int, page_size: int,
    ) -> "tuple[list[AuditEvent], int]":
        raise NotImplementedError


class PostgresAudRepository(AudRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط aud.events (004_aud.sql — مغلَق، بلا تعديل)."""

    def __init__(self, connection):
        self._connection = connection

    @property
    def connection(self):
        return self._connection

    def insert_event(self, event: AuditEvent) -> AuditEvent:
        # REQ-AUD-001..012: إدراج فقط، لا UPDATE/DELETE في أي مسار بهذا الملف
        query = """
            INSERT INTO aud.events
                (log_type, event_name, correlation_id, actor_ref_id, before_value, after_value, reason, metadata)
            VALUES
                (%(log_type)s, %(event_name)s, %(correlation_id)s, %(actor_ref_id)s,
                 %(before_value)s, %(after_value)s, %(reason)s, %(metadata)s)
            RETURNING id, occurred_at_utc
        """
        import json as _json
        with self._connection.cursor() as cur:
            cur.execute(query, {
                "log_type": event.log_type, "event_name": event.event_name,
                "correlation_id": event.correlation_id, "actor_ref_id": event.actor_ref_id,
                "before_value": _json.dumps(event.before_value) if event.before_value is not None else None,
                "after_value": _json.dumps(event.after_value) if event.after_value is not None else None,
                "reason": event.reason,
                "metadata": _json.dumps(event.metadata) if event.metadata is not None else None,
            })
            row = cur.fetchone()
            event.id = row["id"]
            event.occurred_at_utc = row["occurred_at_utc"]
        return event

    def list_events(self, log_type, event_name, actor_ref_id, date_from, date_to, page, page_size):
        # يعتمد على idx_events_type_time (log_type متوفر) وidx_events_actor
        offset = (page - 1) * page_size
        filters = []
        params: dict[str, Any] = {"limit": page_size, "offset": offset}
        if log_type is not None:
            filters.append("log_type = %(log_type)s")
            params["log_type"] = log_type
        if event_name is not None:
            filters.append("event_name = %(event_name)s")
            params["event_name"] = event_name
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
            cur.execute(f"SELECT COUNT(*) AS total FROM aud.events {where_clause}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT id, log_type, event_name, correlation_id, actor_ref_id, occurred_at_utc, "
                f"before_value, after_value, reason, metadata FROM aud.events {where_clause} "
                f"ORDER BY occurred_at_utc DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        items = [
            AuditEvent(
                id=r["id"], log_type=r["log_type"], event_name=r["event_name"],
                correlation_id=r["correlation_id"], actor_ref_id=r["actor_ref_id"],
                occurred_at_utc=r["occurred_at_utc"], before_value=r["before_value"],
                after_value=r["after_value"], reason=r["reason"], metadata=r["metadata"],
            )
            for r in rows
        ]
        return items, total


class _NoOpAudTransaction:
    """تصحيح توازٍ (Parity) — message_api.py وauth_api.py يستخدمان الآن
    `with X_repo.connection:` لضمان معاملة صريحة حول إدراج أحداث التدقيق
    الإلزامية (نفس آلية _NoOpTransaction في auth_repository.py، مُكرَّرة هنا
    عمدًا بدل استيرادها لتفادي اقتران جديد غير ضروري بين وحدتين مستقلتين).
    محاكاة وهمية بلا معاملة حقيقية — تنشر أي استثناء كما هو (__exit__ يُعيد
    False دائمًا)، فيعمل نفس كود الإنتاج بلا فرع خاص للاختبارات الوهمية.
    الذرّية الفعلية (Rollback حقيقي) تُختبَر فقط عبر اختبارات PostgreSQL
    التكاملية، لا هنا — نفس الإقرار الصادق الموثَّق في auth_repository.py."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class InMemoryAudRepository(AudRepository):
    """للاختبارات فقط. Append-Only بنفس مبدأ النسخة الحية — لا أي طريقة تعديل/حذف."""

    def __init__(self):
        self._events: list[AuditEvent] = []
        self._next_id = 1

    @property
    def connection(self):
        return _NoOpAudTransaction()

    def insert_event(self, event: AuditEvent) -> AuditEvent:
        event.id = f"aud-{self._next_id}"
        self._next_id += 1
        event.occurred_at_utc = event.occurred_at_utc or datetime.utcnow()
        self._events.append(event)
        return event

    def list_events(self, log_type, event_name, actor_ref_id, date_from, date_to, page, page_size):
        items = list(reversed(self._events))
        if log_type is not None:
            items = [e for e in items if e.log_type == log_type]
        if event_name is not None:
            items = [e for e in items if e.event_name == event_name]
        if actor_ref_id is not None:
            items = [e for e in items if e.actor_ref_id == actor_ref_id]
        if date_from is not None:
            items = [e for e in items if e.occurred_at_utc >= date_from]
        if date_to is not None:
            items = [e for e in items if e.occurred_at_utc <= date_to]
        total = len(items)
        start = (page - 1) * page_size
        return items[start:start + page_size], total
