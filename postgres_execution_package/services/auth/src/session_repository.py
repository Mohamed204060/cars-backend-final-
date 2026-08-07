"""
session_repository.py — طبقة الوصول للبيانات لجلسات المستخدم (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.7 (معيار Repository الإلزامي)؛ CR-013؛
        Migration 023 (iam.sessions)

نفس بنية auth_repository.py تمامًا: واجهة تجريدية (SessionRepository) لا يعرف
عنها session_service.py شيئًا سوى العقد، تنفيذ فعلي عبر PostgreSQL، وتنفيذ
وهمي في الذاكرة للاختبار دون قاعدة بيانات حقيقية.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

from session_service import Session


class SessionRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه طبقة تكامل REST API (Auth Middleware)."""

    @abstractmethod
    def create_session(self, user_id: str, token_hash: str, expires_at: datetime) -> Session:
        raise NotImplementedError

    @abstractmethod
    def get_active_session_by_token_hash(self, token_hash: str) -> Optional[Session]:
        """يُعيد الجلسة فقط إن كانت غير مُبطَلة (revoked_at IS NULL)؛ فحص
        الانتهاء الزمني (expires_at) مسؤولية session_service.py، لا هنا."""
        raise NotImplementedError

    @abstractmethod
    def touch_session(self, session_id: str, new_expires_at: datetime) -> None:
        """REQ-SEC-004 (Sliding Window): يُحدِّث last_active_at وexpires_at
        عند كل استخدام فعلي للجلسة."""
        raise NotImplementedError

    @abstractmethod
    def revoke_session(self, session_id: str, reason: str) -> None:
        """REQ-SEC-005: إبطال فوري لجلسة واحدة (تسجيل خروج)."""
        raise NotImplementedError

    @abstractmethod
    def revoke_all_sessions_for_user(self, user_id: str, reason: str) -> int:
        """REQ-SEC-005: إبطال فوري لكل جلسات مستخدم (عند حظر الحساب)؛
        يُعيد عدد الجلسات المُبطَلة فعليًا."""
        raise NotImplementedError


class PostgresSessionRepository(SessionRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط iam.sessions (Migration 023)."""

    def __init__(self, connection):
        self._connection = connection

    def create_session(self, user_id: str, token_hash: str, expires_at: datetime) -> Session:
        query = """
            INSERT INTO iam.sessions (user_id, token_hash, expires_at)
            VALUES (%(user_id)s, %(token_hash)s, %(expires_at)s)
            RETURNING id, user_id, token_hash, created_at, last_active_at, expires_at, revoked_at, revoked_reason
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"user_id": user_id, "token_hash": token_hash, "expires_at": expires_at})
            row = cur.fetchone()
        return self._row_to_session(row)

    def get_active_session_by_token_hash(self, token_hash: str) -> Optional[Session]:
        # يعتمد على idx_sessions_active_lookup (فهرس جزئي: revoked_at IS NULL)
        query = """
            SELECT id, user_id, token_hash, created_at, last_active_at, expires_at, revoked_at, revoked_reason
            FROM iam.sessions
            WHERE token_hash = %(token_hash)s AND revoked_at IS NULL
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"token_hash": token_hash})
            row = cur.fetchone()
        return self._row_to_session(row) if row else None

    def touch_session(self, session_id: str, new_expires_at: datetime) -> None:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE iam.sessions SET last_active_at = now(), expires_at = %(expires_at)s "
                    "WHERE id = %(id)s AND revoked_at IS NULL",
                    {"id": session_id, "expires_at": new_expires_at},
                )

    def revoke_session(self, session_id: str, reason: str) -> None:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE iam.sessions SET revoked_at = now(), revoked_reason = %(reason)s "
                    "WHERE id = %(id)s AND revoked_at IS NULL",
                    {"id": session_id, "reason": reason},
                )

    def revoke_all_sessions_for_user(self, user_id: str, reason: str) -> int:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE iam.sessions SET revoked_at = now(), revoked_reason = %(reason)s "
                    "WHERE user_id = %(user_id)s AND revoked_at IS NULL",
                    {"user_id": user_id, "reason": reason},
                )
                return cur.rowcount

    @staticmethod
    def _row_to_session(row) -> Session:
        return Session(
            id=row["id"], user_id=row["user_id"], token_hash=row["token_hash"],
            created_at=row["created_at"], last_active_at=row["last_active_at"],
            expires_at=row["expires_at"], revoked_at=row["revoked_at"], revoked_reason=row["revoked_reason"],
        )


class InMemorySessionRepository(SessionRepository):
    """تنفيذ وهمي للاختبار فقط؛ يحاكي الجدول كقائمة في الذاكرة."""

    def __init__(self):
        self._sessions: List[Session] = []
        self._next_seq = 1

    def create_session(self, user_id: str, token_hash: str, expires_at: datetime) -> Session:
        now = datetime.now(timezone.utc)
        session = Session(
            id=f"session-{self._next_seq}", user_id=user_id, token_hash=token_hash,
            created_at=now, last_active_at=now, expires_at=expires_at,
        )
        self._next_seq += 1
        self._sessions.append(session)
        return session

    def get_active_session_by_token_hash(self, token_hash: str) -> Optional[Session]:
        for s in self._sessions:
            if s.token_hash == token_hash and s.revoked_at is None:
                return s
        return None

    def touch_session(self, session_id: str, new_expires_at: datetime) -> None:
        for s in self._sessions:
            if s.id == session_id and s.revoked_at is None:
                s.last_active_at = datetime.now(timezone.utc)
                s.expires_at = new_expires_at
                return

    def revoke_session(self, session_id: str, reason: str) -> None:
        for s in self._sessions:
            if s.id == session_id and s.revoked_at is None:
                s.revoked_at = datetime.now(timezone.utc)
                s.revoked_reason = reason
                return

    def revoke_all_sessions_for_user(self, user_id: str, reason: str) -> int:
        count = 0
        now = datetime.now(timezone.utc)
        for s in self._sessions:
            if s.user_id == user_id and s.revoked_at is None:
                s.revoked_at = now
                s.revoked_reason = reason
                count += 1
        return count
