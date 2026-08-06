"""
session_service.py — منطق إدارة الجلسات (Session Lifecycle)
المرجع: CR-013 — REQ-SEC-004 (انتهاء الجلسة تلقائيًا عند عدم النشاط)،
        REQ-SEC-005 (إلغاء الجلسة فورًا عند تسجيل الخروج أو الحظر)؛
        Migration 023 (iam.sessions)

هذا الملف يُطبِّق منطق الأعمال المستقل عن قاعدة البيانات (وحدات اختبار خالصة)،
بنفس نمط auth_service.py تمامًا: لا اتصال فعلي بقاعدة بيانات هنا؛ التكامل
الفعلي مع iam.sessions يتم في session_repository.py.

مبدأ أمني جوهري (REQ-SEC-002 امتدادًا): لا يُخزَّن التوكن الخام في أي مكان
مطلقًا — يُولَّد عشوائيًا عالي الإنتروبيا، يُعاد للمستخدم مرة واحدة فقط (عبر
Set-Cookie)، ويُخزَّن على الخادم بصمة SHA-256 له فقط. لا توجد أي دالة في هذا
الملف تعيد التوكن الخام لأي طرف بعد توليده الأول.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# سياسات قابلة للضبط (REQ-SEC-004: "مدة قابلة للضبط"، لا رقمًا معماريًا ثابتًا)
# ---------------------------------------------------------------------------

DEFAULT_IDLE_TIMEOUT_SECONDS = 1800  # 30 دقيقة؛ قيمة افتراضية مقترَحة قابلة للتعديل دون CR جديد (CR-013، القسم 3)
SESSION_TOKEN_BYTES = 32  # إنتروبيا التوكن الخام قبل التجزئة (256-bit)


# ---------------------------------------------------------------------------
# نماذج بيانات مبسَّطة (تعكس مخطط iam.sessions، Migration 023)
# ---------------------------------------------------------------------------

@dataclass
class Session:
    id: str
    user_id: str
    token_hash: str
    created_at: datetime
    last_active_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    revoked_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# استثناءات منطق الأعمال (تُترجَم لاحقًا لاستجابات نموذج الخطأ الموحّد)
# ---------------------------------------------------------------------------

class SessionInvalidError(Exception):
    """لا جلسة مطابقة للتوكن المُقدَّم، أو التوكن غير موجود أصلاً."""


class SessionExpiredError(Exception):
    """REQ-SEC-004: تجاوزت الجلسة مهلة الخمول المسموحة."""


class SessionRevokedError(Exception):
    """REQ-SEC-005: الجلسة أُبطِلت صراحة (تسجيل خروج أو حظر حساب)."""


VALID_REVOCATION_REASONS = {"logout", "idle_timeout", "admin_ban", "admin_revoke"}


# ---------------------------------------------------------------------------
# توليد التوكن وتجزئته
# ---------------------------------------------------------------------------

def generate_session_token() -> str:
    """يولِّد توكنًا عشوائيًا عالي الإنتروبيا (256-bit)، آمنًا للاستخدام كـCookie."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    """SHA-256 hex (64 حرفًا) — يطابق VARCHAR(64) في Migration 023.
    يكفي SHA-256 العادي هنا (لا bcrypt/argon2): التوكن نفسه عالي الإنتروبيا
    عشوائيًا (لا كلمة مرور يختارها إنسان)، فلا حاجة لدالة تجزئة بطيئة عمدًا
    لمقاومة القوة الغاشمة على القاموس؛ هذا يخالف REQ-SEC-002 الخاص بكلمات
    المرور فقط، لا يطبَّق حرفيًا هنا لاختلاف طبيعة القيمة المُجزَّأة."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# دورة حياة الجلسة
# ---------------------------------------------------------------------------

def compute_expiry(now: datetime, idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS) -> datetime:
    return now + timedelta(seconds=idle_timeout_seconds)


def is_expired(session: Session, now: datetime) -> bool:
    return now >= session.expires_at


def is_revoked(session: Session) -> bool:
    return session.revoked_at is not None


def ensure_session_valid(session: Optional[Session], now: datetime) -> Session:
    """REQ-SEC-004/005: يتحقق من صلاحية الجلسة قبل السماح بأي إجراء محمي.
    يُستدعى من طبقة التكامل (Middleware/Dependency) عند كل طلب محمي."""
    if session is None:
        raise SessionInvalidError("لا توجد جلسة مطابقة لهذا التوكن.")
    if is_revoked(session):
        raise SessionRevokedError("هذه الجلسة أُبطِلت (تسجيل خروج أو حظر حساب).")
    if is_expired(session, now):
        raise SessionExpiredError("انتهت هذه الجلسة بسبب عدم النشاط؛ يُرجى تسجيل الدخول من جديد.")
    return session


def build_revocation(reason: str) -> str:
    """يتحقق من صحة سبب الإبطال قبل تمريره لطبقة التخزين (يعكس قيد قاعدة البيانات
    chk_sessions_revoked_reason، Migration 023)."""
    if reason not in VALID_REVOCATION_REASONS:
        raise ValueError(f"سبب إبطال جلسة غير معروف: {reason}")
    return reason
