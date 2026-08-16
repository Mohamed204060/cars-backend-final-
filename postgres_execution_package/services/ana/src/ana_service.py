"""
ana_service.py — طبقة التنسيق لـAnalytics Event Foundation
المرجع: Reporting/Analytics Catalog v1.0 §32

ALLOWED_EVENT_TYPES: حوكمة صريحة (Reporting Data Dictionary principle، §31) —
لا نريد event_type حرًّا غير محدود يتكاثر بلا ضبط. القائمة الابتدائية هنا هي
أمثلة §32 حرفيًا فقط؛ التوسيع لاحقًا (عند ربط Domains فعليًا) هو تعديل قائمة
Python بسيط بلا Migration، عمدًا مصمَّم بهذا الشكل لسهولة التوسّع المستقبلي.

Data Minimization: metadata يجب ألا يحتوي بيانات شخصية حرة (بريد/هاتف/اسم)؛
هذا الملف لا يفرض ذلك آليًا (يحتاج مراجعة عند instrumentation كل Domain لاحقًا)
لكنه يحدّ حجم metadata دفاعًا أوليًا بسيطًا (عدد المفاتيح + الحجم الفعلي معًا).

Corrective Pass (بعد مراجعة الحزمة الأولى): context_ref_id عمود UUID فعليًا
في 033_ana_events.sql؛ التحقق هنا (لا Pydantic UUID type — لا سابقة له في
المشروع، وكل *_ref_id معاملة كـstr مجرَّد في كل مكان) يمنع وصول قيمة غير
صالحة لـPostgreSQL وتحويلها لخطأ 500 غير واضح، بنفس نمط الأخطاء المخصَّصة
المعتمَد هنا أصلًا (InvalidEventTypeError/MetadataTooLargeError).
"""

import json
import uuid as _uuid
from datetime import datetime
from typing import Optional

from ana_repository import AnalyticsEvent

# مطابقة حرفية لأمثلة §32 — تُوسَّع مستقبلًا عند instrumentation كل Domain
ALLOWED_EVENT_TYPES = {
    "search_performed",
    "search_zero_results",
    "search_result_impression",
    "search_result_clicked",
    "inventory_item_viewed",
    "purchase_request_created",
    "offer_submitted",
    "offer_accepted",
}

_MAX_METADATA_KEYS = 20  # حد دفاعي بسيط، Data Minimization
_MAX_METADATA_BYTES = 8192  # 8KB — يمنع قيمة واحدة ضخمة رغم قلة عدد المفاتيح


class InvalidEventTypeError(ValueError):
    pass


class MetadataTooLargeError(ValueError):
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


def _validate_metadata_or_raise(metadata: Optional[dict]) -> None:
    if metadata is None:
        return
    if len(metadata) > _MAX_METADATA_KEYS:
        raise MetadataTooLargeError("metadata يتجاوز الحد المسموح من الحقول.")
    serialized_size = len(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
    if serialized_size > _MAX_METADATA_BYTES:
        raise MetadataTooLargeError(f"metadata يتجاوز الحد المسموح للحجم ({_MAX_METADATA_BYTES} bytes).")


def record_analytics_event_via_repository(
    repository, event_type: str,
    actor_ref_id: Optional[str] = None, session_ref_id: Optional[str] = None,
    context_type: Optional[str] = None, context_ref_id: Optional[str] = None,
    correlation_id: Optional[str] = None, metadata: Optional[dict] = None,
) -> AnalyticsEvent:
    if event_type not in ALLOWED_EVENT_TYPES:
        raise InvalidEventTypeError(f"event_type غير معروف/غير معتمَد: {event_type}")
    _validate_uuid_or_raise(context_ref_id, "context_ref_id")
    _validate_metadata_or_raise(metadata)
    event = AnalyticsEvent(
        id=None, event_type=event_type, occurred_at_utc=None, actor_ref_id=actor_ref_id,
        session_ref_id=session_ref_id, context_type=context_type, context_ref_id=context_ref_id,
        correlation_id=correlation_id, metadata=metadata,
    )
    return repository.insert_event(event)


def list_analytics_events_via_repository(
    repository, event_type: Optional[str] = None, context_type: Optional[str] = None,
    context_ref_id: Optional[str] = None, actor_ref_id: Optional[str] = None,
    date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
    page: int = 1, page_size: int = 20,
):
    if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
        raise InvalidEventTypeError(f"event_type غير معروف/غير معتمَد: {event_type}")
    _validate_uuid_or_raise(context_ref_id, "context_ref_id")
    _validate_uuid_or_raise(actor_ref_id, "actor_ref_id")
    return repository.list_events(event_type, context_type, context_ref_id, actor_ref_id, date_from, date_to, page, page_size)
