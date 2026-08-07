"""
idempotency_repository.py — طبقة الوصول للبيانات لتخزين مفاتيح عدم التكرار
المرجع: 025_idempotency_keys.sql؛ نفس نمط auth_repository.py تمامًا.
"""

from abc import ABC, abstractmethod
from typing import Optional

from idempotency_service import CachedResponse


class IdempotencyRepository(ABC):

    @abstractmethod
    def get_cached_response(self, idempotency_key: str, user_ref_id: str, endpoint: str) -> Optional[CachedResponse]:
        raise NotImplementedError

    @abstractmethod
    def store_response(self, idempotency_key: str, user_ref_id: str, endpoint: str,
                        response_status: int, response_body: dict) -> None:
        raise NotImplementedError


class PostgresIdempotencyRepository(IdempotencyRepository):

    def __init__(self, connection):
        self._connection = connection

    def get_cached_response(self, idempotency_key: str, user_ref_id: str, endpoint: str) -> Optional[CachedResponse]:
        import json
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT response_status, response_body FROM sys.idempotency_keys "
                "WHERE idempotency_key = %(key)s AND user_ref_id = %(user_ref_id)s AND endpoint = %(endpoint)s",
                {"key": idempotency_key, "user_ref_id": user_ref_id, "endpoint": endpoint},
            )
            row = cur.fetchone()
        if row is None:
            return None
        body = row["response_body"]
        if isinstance(body, str):
            body = json.loads(body)
        return CachedResponse(response_status=row["response_status"], response_body=body)

    def store_response(self, idempotency_key: str, user_ref_id: str, endpoint: str,
                        response_status: int, response_body: dict) -> None:
        import json
        with self._connection:
            with self._connection.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO sys.idempotency_keys "
                        "(idempotency_key, user_ref_id, endpoint, response_status, response_body) "
                        "VALUES (%(key)s, %(user_ref_id)s, %(endpoint)s, %(status)s, %(body)s)",
                        {"key": idempotency_key, "user_ref_id": user_ref_id, "endpoint": endpoint,
                         "status": response_status, "body": json.dumps(response_body)},
                    )
                except Exception as exc:
                    # سباق تزامن نادر: طلبان بنفس المفتاح في نفس اللحظة؛ الفائز
                    # الأول يُخزِّن، والثاني يتجاهل الخطأ بأمان (النتيجة الصحيحة
                    # موجودة فعليًا في الجدول من المحاولة الأولى).
                    if "UniqueViolation" not in type(exc).__name__ and "unique" not in str(exc).lower():
                        raise


class InMemoryIdempotencyRepository(IdempotencyRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._store = {}

    def get_cached_response(self, idempotency_key: str, user_ref_id: str, endpoint: str) -> Optional[CachedResponse]:
        return self._store.get((idempotency_key, user_ref_id, endpoint))

    def store_response(self, idempotency_key: str, user_ref_id: str, endpoint: str,
                        response_status: int, response_body: dict) -> None:
        key = (idempotency_key, user_ref_id, endpoint)
        if key not in self._store:
            self._store[key] = CachedResponse(response_status=response_status, response_body=response_body)
