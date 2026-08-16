"""
aud_service.py — طبقة التنسيق لخدمة AUD (سجل التدقيق)
المرجع: REQ-AUD-001..012

هذا الملف عابر للنطاقات عمدًا (Cross-Cutting): لا يعرف شيئًا عن Store أو
Order أو أي Domain آخر — يقبل فقط الحقول التي يبنيها كل Domain بنفسه
(auth_service.build_security_audit_event، store_service.build_administrative_audit_event،
وأي دالة build_*_audit_event مستقبلية بنفس النمط)، ولا يتحقق من event_name
نفسه (تلك مسؤولية الـDomain الذي يبنيه)؛ فقط يتحقق من log_type (القيمة
المشتركة عبر كل الأحداث، مطابقة CHECK constraint في 004_aud.sql حرفيًا) قبل
الوصول لقاعدة البيانات (Fail Fast، دفاع مضاعف مع الـCHECK).

ربط الاستدعاء الفعلي من auth_api.py/store_api.py (استهلاك build_*_audit_event
+ استدعاء record_audit_event_via_repository) مؤجَّل عمدًا لدفعة لاحقة، لتفادي
تعديل أي وحدة مغلقة الآن بلا Failure فعلي يبرر ذلك.

Corrective Pass: actor_ref_id عمود UUID فعليًا في aud.events (004_aud.sql)؛
نفس تحقُّق context_ref_id المضاف في ana_service.py، بنفس المبدأ (لا يصل
Postgres قيمة غير صالحة فتتحول لـ500 غير واضح).
"""

import uuid as _uuid
from datetime import datetime
from typing import Optional

from aud_repository import AuditEvent

ALLOWED_LOG_TYPES = {"general", "security", "administrative"}  # مطابق حرفيًا لـ chk_events_log_type


class InvalidLogTypeError(ValueError):
    pass


class InvalidRefIdError(ValueError):
    pass


def _validate_uuid_or_raise(value: Optional[str], field_name: str) -> None:
    if value is None:
        return
    try:
        _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise InvalidRefIdError(f"{field_name} ليس UUID صالحًا: {value}")


def record_audit_event_via_repository(
    repository, log_type: str, event_name: str,
    actor_ref_id: Optional[str] = None, correlation_id: Optional[str] = None,
    before_value: Optional[dict] = None, after_value: Optional[dict] = None,
    reason: Optional[str] = None, metadata: Optional[dict] = None,
) -> AuditEvent:
    if log_type not in ALLOWED_LOG_TYPES:
        raise InvalidLogTypeError(f"log_type غير معروف: {log_type}")
    _validate_uuid_or_raise(actor_ref_id, "actor_ref_id")
    event = AuditEvent(
        id=None, log_type=log_type, event_name=event_name, correlation_id=correlation_id,
        actor_ref_id=actor_ref_id, occurred_at_utc=None, before_value=before_value,
        after_value=after_value, reason=reason, metadata=metadata,
    )
    return repository.insert_event(event)


def list_audit_events_via_repository(
    repository, log_type: Optional[str] = None, event_name: Optional[str] = None,
    actor_ref_id: Optional[str] = None, date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None, page: int = 1, page_size: int = 20,
):
    if log_type is not None and log_type not in ALLOWED_LOG_TYPES:
        raise InvalidLogTypeError(f"log_type غير معروف: {log_type}")
    _validate_uuid_or_raise(actor_ref_id, "actor_ref_id")
    return repository.list_events(log_type, event_name, actor_ref_id, date_from, date_to, page, page_size)
