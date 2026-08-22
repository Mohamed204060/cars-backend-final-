"""
auth_api.py — طبقة REST API لخدمة Auth (Controllers)
المرجع: Auth Service — Contract Extension & Implementation Plan (المعتمَدة)؛
        api_spec/openapi.yaml v1.1.0؛ CR-013 (iam.sessions)؛
        دليل حوكمة التنفيذ v1.7 (سلسلة الاعتماد API -> Service -> Repository)

اختيار الإطار (FastAPI): لا يوجد أي إطار ويب مُعتمَد سابقًا في الـBaseline
(لا Flask ولا Django ولا أي controllers/routes قائمة) — هذا أول كود REST
في المشروع. اخُتير FastAPI تحديدًا لأنه: (1) يتكامل مباشرة مع Pydantic
لتوليد/التحقق من نماذج الطلب والاستجابة المطابقة لـopenapi.yaml حرفيًا،
(2) يُصدر مواصفة OpenAPI ذاتيًا من الكود قابلة للمقارنة بالعقد المعتمَد
كخطوة تحقق إضافية، (3) خفيف الاعتماديات (لا يستوجب ORM أو بنية مشروع
مفروضة تتعارض مع طبقات Service/Repository القائمة). هذا اختيار تنفيذي
لا يمس أي قرار معماري موثَّق في SAD/DD؛ قابل لإعادة النظر عبر ADR إن
استدعى الأمر لاحقًا.

هذا الملف يفترض حقن الاعتماديات التالية عند التركيب الفعلي (main.py، خارج
نطاق هذا الملف): اتصال PostgreSQL (psycopg2) يُبنى منه AuthRepository
وSessionRepository الفعليَّين.
"""

import os
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from auth_service import (
    DuplicateIdentityError,
    InvalidPasswordCredentialsError,
    LastIdentityRemovalError,
    ProviderDisabledError,
    add_identity_via_repository,
    build_login_security_audit_event,
    build_security_audit_event,
    login_with_password_via_repository,
    remove_identity_via_repository,
)
from session_service import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    Session,
    SessionExpiredError,
    SessionInvalidError,
    SessionRevokedError,
    compute_expiry,
    ensure_session_valid,
    generate_session_token,
    hash_token,
)
# CR-018: store_service (لا store_api) عمدًا — store_api.py يستورد أصلًا من
# auth_api.py (get_current_session وغيرها)؛ استيراد عكسي هنا يُنشئ Circular
# Import. store_service.py وحدة خدمة نقية بلا اعتماد على auth_api، آمنة.
from store_service import create_store
from registration_service import InvalidRegistrationChoiceError, register_user
from password_policy import WeakPasswordError
from client_ip import resolve_authoritative_client_ip
from identifier_hmac import compute_attempted_identifier_hmac
from aud_repository import AuditEvent

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# نفس مجموعة pct_api.SYSTEM_ADMIN_ROLES حرفيًا — لكن مُكرَّرة هنا عمدًا لا
# مستوردة، لأن pct_api.py يستورد أصلًا من auth_api.py (error/get_correlation_id/
# get_current_session/get_optional_session)؛ استيراد عكسي هنا يُنشئ Circular
# Import (نفس التحذير الموثَّق أعلاه لـCR-018 مع store_api.py).
SYSTEM_ADMIN_ROLES = {"super_admin", "admin"}

SESSION_COOKIE_NAME = "session_id"
# REQ-SEC-004: مهلة قابلة للضبط عبر متغيّر بيئة، لا رقمًا معماريًا ثابتًا (CR-013، القسم 3)
IDLE_TIMEOUT_SECONDS = int(os.environ.get("SESSION_IDLE_TIMEOUT_SECONDS", DEFAULT_IDLE_TIMEOUT_SECONDS))
# REQ-SEC-005: Cookie آمنة إلزاميًا؛ Secure يُعطَّل فقط في بيئة تطوير محلية صريحة
COOKIE_SECURE = os.environ.get("SESSION_COOKIE_INSECURE_DEV_ONLY", "false").lower() != "true"


def get_aud_repository(request: Request):
    """نفس نمط الوصول عبر app.state المتّبع في كل الملفات الأخرى (rpt_api.py،
    media_api.py، إلخ) — لا استيراد مباشر من aud_api.py لتفادي أي احتمال
    لاعتماد دائري، رغم عدم وجود ما يمنعه فعليًا هنا؛ نفس القاعدة العامة."""
    return request.app.state.aud_repository


class SecurityEventPersistenceError(Exception):
    """Login/Security History (Gap Sweep v2.2/الجولة الأمنية الأخيرة):
    فشل تسجيل حدث دخول إلزامي — سجل الدخول التاريخي ليس Telemetry اختياريًا.
    تُرفَع صراحةً (لا تُبتلَع) من _record_login_security_event، وتُترجَم في
    كل موضع استدعاء (login/register) إلى استجابة خطأ منسَّقة صريحة — لا
    استثناء غير مُعالَج يتسرَّب كـ500 خام، ولا نجاح صامت لعملية دخول لم
    يُسجَّل دليلها التاريخي الإلزامي فعليًا."""


def _record_login_security_event(
    request: Request,
    aud_repo,
    correlation_id: str,
    outcome: str,
    user_id: Optional[str] = None,
    attempted_identifier: Optional[str] = None,
) -> None:
    """
    Admin Operational Completion — Login/Security History. هذه الدالة الآن
    Fail-Closed عمدًا (تصحيح أمني مُلزِم): لا ابتلاع للاستثناءات هنا إطلاقًا.
    سجل الدخول التاريخي (وقت + IP موثوق) مطلب مُعتمَد غير قابل للتنازل — لا
    "Best-Effort Telemetry". فشل الإدراج (اتصال قاعدة بيانات، أو سرّ HMAC
    غائب لمحاولة دخول فاشلة تحمل معرِّفًا) يجب أن يمنع اعتبار عملية الدخول
    ناجحة بالكامل، لا أن يُسجَّل الحدث بحقل ناقص بصمت.

    ترتيب الاستدعاء في login/register (بعد إنشاء الجلسة، قبل ضبط الكوكي)
    يضمن فعليًا أن فشل هذه الدالة يمنع الوصول لـset_session_cookie — لا
    كوكي جلسة صالحة تصل العميل أبدًا إن تعذّر تسجيل الدليل التاريخي
    الإلزامي، حتى لو أُدرِج صف جلسة في iam.sessions فعليًا.
    """
    try:
        ip_address = resolve_authoritative_client_ip(
            peer_address=request.client.host if request.client else None,
            forwarded_for_header=request.headers.get("x-forwarded-for"),
        )
        attempted_identifier_hmac = None
        if outcome == "login_failed" and attempted_identifier:
            # MissingHmacSecretError تتسرَّب هنا عمدًا بلا التقاط — سرّ HMAC
            # غائب لمحاولة تحمل معرِّفًا هو فشل تهيئة/أمان حقيقي، لا سببًا
            # لتخفيض الحدث الأمني بصمت (لا None هنا بديلًا آمنًا مقبولًا).
            attempted_identifier_hmac = compute_attempted_identifier_hmac(attempted_identifier)

        event_dict = build_login_security_audit_event(
            outcome=outcome, ip_address=ip_address, user_id=user_id,
            attempted_identifier_hmac=attempted_identifier_hmac,
        )
        aud_repo.insert_event(AuditEvent(
            id=None, log_type=event_dict["log_type"], event_name=event_dict["event_name"],
            correlation_id=correlation_id, actor_ref_id=event_dict["actor_ref_id"],
            occurred_at_utc=None, before_value=None, after_value=None, reason=None,
            metadata=event_dict["metadata"],
        ))
    except Exception as exc:
        raise SecurityEventPersistenceError(
            f"تعذّر تسجيل دليل الدخول التاريخي الإلزامي ({outcome}): {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# نماذج الطلب/الاستجابة (Pydantic) — تطابق حرفيًا مكوّنات openapi.yaml
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    field: Optional[str] = None
    reason: Optional[str] = None


class ErrorResponse(BaseModel):
    correlation_id: str
    error_code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class LoginRequest(BaseModel):
    login_identifier: str
    password: str


class LoginResponse(BaseModel):
    primary_role: Optional[str] = None
    account_status: Optional[str] = None


class RegisterRequest(BaseModel):
    """CR-018: role_choice/account_type مقيَّدان بنيويًا بـLiteral — لا قيمة
    إدارية (admin/moderator/...) قابلة للوصول عبر هذا المسار إطلاقًا، بصرف
    النظر عن أي فحص إضافي في registration_service.py."""
    role_choice: Literal["buyer", "seller"]
    account_type: Literal["individual", "business"]
    email: str
    password: str


class RegisterResponse(BaseModel):
    user_id: str
    primary_role: str
    account_type: str
    store_id: Optional[str] = None


class LogoutResponse(BaseModel):
    status: str = "logged_out"


class MeResponse(BaseModel):
    """CR-016: GET /auth/me — الحد الأدنى المطلوب فقط، لا توسيع نطاق."""
    user_id: str
    primary_role: str
    account_status: str


class IdentitySummary(BaseModel):
    id: str
    provider_code: str
    is_verified: bool
    is_primary: bool
    last_authenticated_at: Optional[datetime] = None


class IdentityListResponse(BaseModel):
    identities: list[IdentitySummary]


class IdentityAddRequest(BaseModel):
    provider_code: str
    external_identifier: str
    is_verified: bool = False


class IdentityAddResponse(BaseModel):
    id: str
    provider_code: str
    is_verified: bool


class ProviderSummary(BaseModel):
    code: str
    display_name: str
    category: str


class ProviderListResponse(BaseModel):
    providers: list[ProviderSummary]


# ---------------------------------------------------------------------------
# مساعدات مشترَكة: Correlation ID، نموذج الخطأ الموحّد (DD الحزمة 2، القسم 2.4)
# ---------------------------------------------------------------------------

def get_correlation_id(x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id")) -> str:
    return x_correlation_id or str(uuid4())


def error(correlation_id: str, status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ErrorResponse(correlation_id=correlation_id, error_code=error_code, message=message).model_dump(),
        headers={"X-Correlation-Id": correlation_id},
    )


# ---------------------------------------------------------------------------
# اعتماديات حقن (تُموَّن فعليًا من main.py عند التركيب؛ توقيع placeholder هنا)
# ---------------------------------------------------------------------------

def get_auth_repository(request: Request):
    """يُستبدَل فعليًا عبر dependency_overrides في main.py باتصال PostgreSQL حقيقي،
    أو بـInMemoryAuthRepository في الاختبارات."""
    return request.app.state.auth_repository


def get_session_repository(request: Request):
    return request.app.state.session_repository


def get_store_repository_for_registration(request: Request):
    """CR-018: نسخة محلية مكرَّرة عمدًا من get_store_repository في
    store_api.py — استيرادها من هناك مباشرة كان سيُنشئ Circular Import
    (store_api.py يستورد من auth_api.py أصلًا). دالتان بسطر واحد، لا خطر
    اختلاف سلوك حقيقي بينهما."""
    return request.app.state.store_repository


async def get_current_session(
    correlation_id: str = Depends(get_correlation_id),
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    session_repo=Depends(get_session_repository),
) -> Session:
    """REQ-IAM-003: بوابة التحقق الإلزامية قبل أي عملية محمية.
    REQ-SEC-004: يُحدِّث انتهاء الصلاحية (Sliding Window) عند كل استخدام ناجح."""
    if not session_id:
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "NO_SESSION", "لا توجد جلسة نشطة؛ يُرجى تسجيل الدخول.")

    token_hash = hash_token(session_id)
    live_session = session_repo.get_active_session_by_token_hash(token_hash)

    try:
        valid_session = ensure_session_valid(live_session, datetime.now(timezone.utc))
    except SessionInvalidError:
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "NO_SESSION", "لا توجد جلسة نشطة؛ يُرجى تسجيل الدخول.")
    except SessionRevokedError:
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "SESSION_REVOKED", "انتهت هذه الجلسة (تسجيل خروج أو حظر حساب).")
    except SessionExpiredError:
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "SESSION_EXPIRED", "انتهت الجلسة بسبب عدم النشاط؛ يُرجى تسجيل الدخول من جديد.")

    new_expiry = compute_expiry(datetime.now(timezone.utc), IDLE_TIMEOUT_SECONDS)
    session_repo.touch_session(valid_session.id, new_expiry)
    return valid_session


async def get_optional_session(
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    session_repo=Depends(get_session_repository),
) -> Optional[Session]:
    """
    CR-015: بديل غير إلزامي لـget_current_session — لعمليات عامة بطبيعتها
    (لا 401 لغياب الجلسة) لكن سلوكها يختلف إن وُجدت جلسة صالحة (مثل
    GET /pct/parts: عام لـapproved، إداري لـproposed). يعيد None بصمت لأي
    غياب/عدم صلاحية للجلسة — لا يرفع HTTPException إطلاقًا، خلافًا للدالة
    الإلزامية أعلاه. لا Sliding Window هنا عمدًا (لا touch_session) — لتفادي
    تحديث انتهاء الصلاحية على كل نداء عام لا يتطلب فعليًا جلسة نشطة.
    """
    if not session_id:
        return None
    token_hash = hash_token(session_id)
    live_session = session_repo.get_active_session_by_token_hash(token_hash)
    try:
        return ensure_session_valid(live_session, datetime.now(timezone.utc))
    except (SessionInvalidError, SessionRevokedError, SessionExpiredError):
        return None


def set_session_cookie(response: Response, raw_token: str) -> None:
    """REQ-SEC-005 وCR-013: HttpOnly + Secure + SameSite إلزاميًا؛ التوكن
    الخام لا يُعاد إلا هنا، مرة واحدة، ولا يظهر أبدًا في جسم أي استجابة JSON."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=IDLE_TIMEOUT_SECONDS,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, httponly=True, secure=COOKIE_SECURE, samesite="lax")


# ---------------------------------------------------------------------------
# POST /auth/login — REQ-IAM-003، REQ-SEC-004/007
# ---------------------------------------------------------------------------

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    response: Response,
    request: Request,
    correlation_id: str = Depends(get_correlation_id),
    auth_repo=Depends(get_auth_repository),
    store_repo=Depends(get_store_repository_for_registration),
    session_repo=Depends(get_session_repository),
    aud_repo=Depends(get_aud_repository),
):
    """
    CR-018 — REQ-IAM-001/002/006، REQ-STR-001، REQ-SEC-006. طبقة رقيقة فقط:
    كل منطق Mapping/المعاملة/Business Rules في registration_service.py،
    لا هنا. بعد النجاح: تسجيل دخول تلقائي بنفس آلية POST /login حرفيًا
    (نفس الدوال: generate_session_token/hash_token/compute_expiry/
    session_repo.create_session/set_session_cookie) — لا آلية جلسة جديدة.

    الحد الفاصل الذري الموثَّق (مطلوب صراحة — لا تخمين): تحقَّقتُ من
    registration_service.py مباشرة — register_user() يُغلِّف إنشاء
    iam.users + str.stores (عند الاقتضاء) داخل `with auth_repo.connection:`
    واحدة (نفس كائن الاتصال المشترَك)؛ هذا يُثبِّت (Commit) الحساب/المتجر
    **قبل** عودة register_user() هنا — لا رجوع ممكن عن هذا الجزء بعد هذه
    النقطة، بأي آلية معقولة الحجم.

    جلسة الدخول (session_repo.create_session) وحدث login_success
    (aud.events) — تصحيح نهائي: يُنفَّذان الآن معًا داخل معاملة صريحة
    واحدة (`with auth_repo.connection:`، نفس الآلية المستخدَمة أعلاه
    حرفيًا لـuser/store — auth_repo.connection في PostgresAuthRepository
    خاصية حقيقية تُعيد نفس كائن psycopg2.connection المشترَك فعليًا بين كل
    الـRepositories المحقونة في هذا الطلب، مؤكَّد من قراءة الكود مباشرة؛
    وفي InMemoryAuthRepository تُعيد _NoOpTransaction التي تنشر أي استثناء
    كما هو (__exit__ يُعيد False دائمًا) فيعمل نفس المسار بلا فرع خاص
    للاختبارات). فشل إدراج الحدث الأمني يُسقِط `with` بالكامل، فيُنفَّذ
    Rollback الحقيقي (لا Compensation لاحقة، لا احتمال معاملة "Aborted"
    تُفشِل محاولة إبطال لاحقة — لا محاولة لاحقة إطلاقًا) — أي أن صف الجلسة
    **لا يُثبَّت في القاعدة أصلًا**، لا "يُنشَأ ثم يُبطَل". هذا يحقق حرفيًا
    الحل المفضَّل المطلوب: معاملة واحدة تُلغي الجلسة تلقائيًا عند فشل
    التدقيق، لا Compensation منفصلة عرضة للفشل بدورها.
    """
    try:
        result = register_user(
            auth_repo, store_repo, create_store,
            role_choice=body.role_choice, account_type=body.account_type,
            email=body.email, raw_password=body.password,
        )
    except WeakPasswordError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "WEAK_PASSWORD", str(exc))
    except InvalidRegistrationChoiceError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_REGISTRATION_CHOICE", str(exc))
    except DuplicateIdentityError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "IDENTITY_ALREADY_EXISTS", str(exc))

    raw_token = generate_session_token()
    token_hash = hash_token(raw_token)
    expires_at = compute_expiry(datetime.now(timezone.utc), IDLE_TIMEOUT_SECONDS)
    try:
        with auth_repo.connection:
            session_repo.create_session(user_id=result.user_id, token_hash=token_hash, expires_at=expires_at)
            _record_login_security_event(request, aud_repo, correlation_id, "login_success", user_id=result.user_id)
    except SecurityEventPersistenceError as exc:
        # لا كوكي جلسة يصل العميل. صف الجلسة نفسه غير مُثبَّت في القاعدة
        # إطلاقًا (Rollback الحقيقي أعلاه) — لا Compensation لاحقة مطلوبة
        # ولا ممكنة الفشل بدورها. الحساب (user_id) يبقى موجودًا فعليًا
        # (حقيقة من الحد الفاصل الموثَّق أعلاه) — رمز خطأ مختلف صراحةً عن
        # فشل تسجيل الدخول العادي، يتضمن user_id الحقيقي، حتى لا يكون
        # العميل في حالة غامضة: عليه استخدام /login مباشرة، لا إعادة
        # محاولة /register (سيصطدم بـ409 حتمًا).
        raise error(
            correlation_id, status.HTTP_503_SERVICE_UNAVAILABLE, "REGISTRATION_SUCCEEDED_SESSION_FAILED",
            f"تم إنشاء الحساب فعلًا (user_id={result.user_id}) لكن تعذّر تسجيل جلسة الدخول الأولى "
            f"بأمان — الحساب غير مفقود، يرجى تسجيل الدخول عبر /login مباشرة بدل إعادة التسجيل. "
            f"التفاصيل: {exc}",
        )

    set_session_cookie(response, raw_token)
    response.headers["X-Correlation-Id"] = correlation_id
    return RegisterResponse(
        user_id=result.user_id, primary_role=result.primary_role,
        account_type=result.account_type, store_id=result.store_id,
    )


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    response: Response,
    request: Request,
    correlation_id: str = Depends(get_correlation_id),
    auth_repo=Depends(get_auth_repository),
    session_repo=Depends(get_session_repository),
    aud_repo=Depends(get_aud_repository),
):
    # تعديل CR-013 v2 — REQ-SEC-002/006: تحقق حقيقي من كلمة المرور؛ لا إنشاء
    # حساب تلقائيًا عند عدم وجود الهوية (بخلاف تدفق OAuth register-or-login).
    # رسالة فشل واحدة موحَّدة دائمًا مهما كان السبب الفعلي، لمنع تعداد الحسابات.
    try:
        identity = login_with_password_via_repository(
            auth_repo, login_identifier=body.login_identifier, raw_password=body.password,
        )
    except InvalidPasswordCredentialsError:
        # Login/Security History: فشل حقيقي يُسجَّل فورًا — actor_ref_id=None
        # (لا تُحلَّل هوية حقيقية موثوقة عند فشل التحقق، سواء كان السبب
        # معرِّفًا غير موجود أو كلمة مرور خاطئة؛ رسالة الفشل الموحَّدة تمنع
        # تعداد الحسابات، والتسجيل هنا يحافظ على نفس المبدأ — لا نكشف عبر
        # aud.events ما لا يُكشَف عبر HTTP). المعرِّف المحاوَل نفسه لا
        # يُخزَّن خامًا أبدًا — فقط HMAC.
        #
        # تصحيح ثبات (Durability) نهائي: PostgresAudRepository.insert_event
        # لا تُثبِّت (Commit) بمفردها إطلاقًا (مؤكَّد من قراءة الكود مباشرة:
        # `with self._connection.cursor()`، لا `with self._connection:`).
        # بلا معاملة صريحة هنا، رؤية الصف على نفس الاتصال (كما في اختبار
        # سابق) لا تُثبِت الثبات الفعلي — قد يبقى الصف معلَّقًا بلا Commit
        # حتى نهاية غامضة لدورة الطلب خارج هذا المستودع. نفس آلية مسار
        # النجاح تمامًا الآن: with auth_repo.connection: يُغلِّف الإدراج،
        # فيُثبَّت فعليًا قبل إعادة 401، أو يُسقَط بالكامل (Rollback حقيقي)
        # ويُستبدَل بـ503 صريح إن فشل — لا نجاح صامت لتسجيل غير ثابت.
        try:
            with auth_repo.connection:
                _record_login_security_event(
                    request, aud_repo, correlation_id, "login_failed",
                    user_id=None, attempted_identifier=body.login_identifier,
                )
        except SecurityEventPersistenceError as exc:
            raise error(correlation_id, status.HTTP_503_SERVICE_UNAVAILABLE, "LOGIN_HISTORY_PERSISTENCE_FAILED", str(exc))
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "بيانات الاعتماد غير صحيحة.")

    user_id = identity.user_id
    raw_token = generate_session_token()
    token_hash = hash_token(raw_token)
    expires_at = compute_expiry(datetime.now(timezone.utc), IDLE_TIMEOUT_SECONDS)
    # تصحيح نهائي: معاملة صريحة واحدة (auth_repo.connection — نفس كائن
    # psycopg2.connection المشترَك فعليًا بين كل الـRepositories المحقونة
    # هنا، مؤكَّد من قراءة الكود مباشرة؛ InMemory تحصل على _NoOpTransaction
    # التي تنشر أي استثناء بلا إخماد) تُغلِّف إنشاء الجلسة + تسجيل الحدث
    # الأمني معًا. فشل التدقيق يُسقِط `with` بالكامل → Rollback حقيقي → صف
    # الجلسة لا يُثبَّت في القاعدة أصلًا (لا "يُنشَأ ثم يُبطَل" عبر
    # Compensation لاحقة قد تفشل بدورها على معاملة "Aborted" — لا محاولة
    # لاحقة إطلاقًا هنا، فلا احتمال لذلك الفشل من الأساس).
    try:
        with auth_repo.connection:
            session_repo.create_session(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
            _record_login_security_event(request, aud_repo, correlation_id, "login_success", user_id=user_id)
    except SecurityEventPersistenceError as exc:
        raise error(correlation_id, status.HTTP_503_SERVICE_UNAVAILABLE, "LOGIN_HISTORY_PERSISTENCE_FAILED", str(exc))

    set_session_cookie(response, raw_token)
    response.headers["X-Correlation-Id"] = correlation_id
    return LoginResponse(primary_role=None, account_status=None)


# ---------------------------------------------------------------------------
# GET /auth/me — CR-016 (Session Introspection)
# الهدف الوحيد: تمكين الواجهة من معرفة هوية/دور المستخدم الحالي من الجلسة
# القائمة فعليًا (بعد Refresh/SSR/فتح مباشر/تبويب جديد) — لا تغيير على آلية
# المصادقة نفسها، لا Token جديد، لا كشف Cookie لجافاسكربت.
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MeResponse)
def get_me(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    auth_repo=Depends(get_auth_repository),
):
    """
    Session صالحة (get_current_session لا يرفع استثناء) → 200 مع بيانات
    المستخدم. Session غائبة/غير صالحة/منتهية → 401 (تُرفَع تلقائيًا من
    get_current_session نفسها، نفس آلية كل Endpoint محمي آخر في المشروع —
    لا منطق جديد هنا).
    """
    result = auth_repo.get_user_role_and_status(current_session.user_id)
    if result is None:
        # حالة نظرية فقط: جلسة صالحة تشير لمستخدم لم يعد موجودًا في iam.users
        # (لا ينبغي حدوثها فعليًا لعدم وجود حذف فعلي لمستخدمين في المشروع،
        # لكن لا نفترض عدم حدوثها صمتًا).
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "SESSION_USER_NOT_FOUND",
                    "الجلسة صالحة لكن المستخدم المرتبط بها غير موجود.")
    primary_role, account_status = result
    return MeResponse(user_id=current_session.user_id, primary_role=primary_role, account_status=account_status)


# ---------------------------------------------------------------------------
# POST /auth/users/{userId}/status — Admin Operational Completion:
# Members/Accounts Administration (Gap Sweep v2/v2.2، بند 4/7)
#
# نطاق مقصود وصريح: Account Status فقط (active/suspended/banned/archived —
# القيد الموجود أصلًا في iam.users، لا قيمة مُخترَعة). لا صلاحية أدوار هنا
# إطلاقًا (Privileged/System-Role Assignment يبقى محظورًا صراحةً حتى تُحسَم
# حوكمته — Gap Sweep v2.2 بند 6/7. هذا Endpoint لا يقبل ولا يعالج primary_role
# بأي شكل، لا نية تدريجية لتوسيعه لاحقًا بلا مراجعة منفصلة).
# ---------------------------------------------------------------------------

ACCOUNT_STATUS_VALUES = {"active", "suspended", "banned", "archived"}


class UserStatusUpdateRequest(BaseModel):
    status: str


class UserStatusUpdateResponse(BaseModel):
    user_id: str
    status: str


@router.post("/users/{user_id}/status", response_model=UserStatusUpdateResponse)
def update_user_status(
    user_id: str,
    body: UserStatusUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    auth_repo=Depends(get_auth_repository),
):
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")

    if body.status not in ACCOUNT_STATUS_VALUES:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_STATUS",
                    f"قيمة status غير معروفة. القيم المسموحة: {sorted(ACCOUNT_STATUS_VALUES)}")

    updated = auth_repo.set_user_status(user_id, body.status)
    if not updated:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND", "المستخدم غير موجود.")

    return UserStatusUpdateResponse(user_id=user_id, status=body.status)


# ---------------------------------------------------------------------------
# POST /auth/logout — REQ-SEC-005، REQ-AUD-005
# ---------------------------------------------------------------------------

@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    session_repo=Depends(get_session_repository),
):
    session_repo.revoke_session(current_session.id, "logout")
    clear_session_cookie(response)
    response.headers["X-Correlation-Id"] = correlation_id
    return LogoutResponse(status="logged_out")


# ---------------------------------------------------------------------------
# GET /auth/identities — REQ-IAM-016
# ---------------------------------------------------------------------------

@router.get("/identities", response_model=IdentityListResponse)
def list_my_identities(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    auth_repo=Depends(get_auth_repository),
):
    identities = auth_repo.get_identities_for_user(current_session.user_id)
    return IdentityListResponse(identities=[
        IdentitySummary(
            id=i.id, provider_code=i.provider_code, is_verified=i.is_verified,
            is_primary=i.is_primary, last_authenticated_at=i.last_authenticated_at,
        ) for i in identities
    ])


# ---------------------------------------------------------------------------
# POST /auth/identities — REQ-IAM-015، REQ-IAM-017
# ---------------------------------------------------------------------------

@router.post("/identities", response_model=IdentityAddResponse, status_code=status.HTTP_201_CREATED)
def add_my_identity(
    body: IdentityAddRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    auth_repo=Depends(get_auth_repository),
):
    try:
        new_identity = add_identity_via_repository(
            auth_repo, user_id=current_session.user_id, provider_code=body.provider_code,
            external_identifier=body.external_identifier, is_verified=body.is_verified,
        )
    except ProviderDisabledError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "PROVIDER_DISABLED", "وسيلة الهوية هذه غير متاحة حاليًا للربط.")
    except DuplicateIdentityError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "IDENTITY_ALREADY_LINKED", str(exc))

    # REQ-IAM-017: تسجيل حدث أمني (يُرسَل لطبقة AUD؛ التسليم الفعلي خارج نطاق هذا الملف)
    build_security_audit_event("identity_added", current_session.user_id, body.provider_code)

    return IdentityAddResponse(id=new_identity.id, provider_code=new_identity.provider_code, is_verified=new_identity.is_verified)


# ---------------------------------------------------------------------------
# DELETE /auth/identities/{identityId} — REQ-IAM-016، REQ-IAM-017
# ---------------------------------------------------------------------------

@router.delete("/identities/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_my_identity(
    identity_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    auth_repo=Depends(get_auth_repository),
):
    try:
        remove_identity_via_repository(auth_repo, user_id=current_session.user_id, identity_id=identity_id)
    except LastIdentityRemovalError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "LAST_IDENTITY", str(exc))
    except ValueError:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "IDENTITY_NOT_FOUND", "وسيلة الهوية غير موجودة لهذا الحساب.")

    # REQ-IAM-017: تسجيل حدث أمني
    build_security_audit_event("identity_removed", current_session.user_id, provider_code="")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# GET /auth/providers — REQ-IAM-013 (بلا مصادقة عمدًا)
# ---------------------------------------------------------------------------

@router.get("/providers", response_model=ProviderListResponse)
def list_enabled_providers(
    correlation_id: str = Depends(get_correlation_id),
    auth_repo=Depends(get_auth_repository),
):
    providers = auth_repo.get_enabled_providers()
    return ProviderListResponse(providers=[
        ProviderSummary(code=p.code, display_name=p.display_name, category=p.category) for p in providers
    ])
