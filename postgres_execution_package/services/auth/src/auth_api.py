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

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SESSION_COOKIE_NAME = "session_id"
# REQ-SEC-004: مهلة قابلة للضبط عبر متغيّر بيئة، لا رقمًا معماريًا ثابتًا (CR-013، القسم 3)
IDLE_TIMEOUT_SECONDS = int(os.environ.get("SESSION_IDLE_TIMEOUT_SECONDS", DEFAULT_IDLE_TIMEOUT_SECONDS))
# REQ-SEC-005: Cookie آمنة إلزاميًا؛ Secure يُعطَّل فقط في بيئة تطوير محلية صريحة
COOKIE_SECURE = os.environ.get("SESSION_COOKIE_INSECURE_DEV_ONLY", "false").lower() != "true"


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
    correlation_id: str = Depends(get_correlation_id),
    auth_repo=Depends(get_auth_repository),
    store_repo=Depends(get_store_repository_for_registration),
    session_repo=Depends(get_session_repository),
):
    """
    CR-018 — REQ-IAM-001/002/006، REQ-STR-001، REQ-SEC-006. طبقة رقيقة فقط:
    كل منطق Mapping/المعاملة/Business Rules في registration_service.py،
    لا هنا. بعد النجاح: تسجيل دخول تلقائي بنفس آلية POST /login حرفيًا
    (نفس الدوال: generate_session_token/hash_token/compute_expiry/
    session_repo.create_session/set_session_cookie) — لا آلية جلسة جديدة.
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
    session_repo.create_session(user_id=result.user_id, token_hash=token_hash, expires_at=expires_at)

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
    correlation_id: str = Depends(get_correlation_id),
    auth_repo=Depends(get_auth_repository),
    session_repo=Depends(get_session_repository),
):
    # تعديل CR-013 v2 — REQ-SEC-002/006: تحقق حقيقي من كلمة المرور؛ لا إنشاء
    # حساب تلقائيًا عند عدم وجود الهوية (بخلاف تدفق OAuth register-or-login).
    # رسالة فشل واحدة موحَّدة دائمًا مهما كان السبب الفعلي، لمنع تعداد الحسابات.
    try:
        identity = login_with_password_via_repository(
            auth_repo, login_identifier=body.login_identifier, raw_password=body.password,
        )
    except InvalidPasswordCredentialsError:
        raise error(correlation_id, status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "بيانات الاعتماد غير صحيحة.")

    user_id = identity.user_id
    raw_token = generate_session_token()
    token_hash = hash_token(raw_token)
    expires_at = compute_expiry(datetime.now(timezone.utc), IDLE_TIMEOUT_SECONDS)
    session_repo.create_session(user_id=user_id, token_hash=token_hash, expires_at=expires_at)

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
