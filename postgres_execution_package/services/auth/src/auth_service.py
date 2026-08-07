"""
auth_service.py — منطق خدمة الهوية والمصادقة (IAM)
المرجع: CR-005 — REQ-IAM-010..017؛ Migration 015/016

هذا الملف يُطبِّق منطق الأعمال المستقل عن قاعدة البيانات (وحدات اختبار خالصة)؛
لا اتصال فعلي بقاعدة بيانات أو مزوّدي OAuth حقيقيين هنا. التكامل الفعلي مع
iam.identity_providers وiam.user_identities يتم في طبقة الوصول للبيانات في
مرحلة لاحقة، خارج نطاق هذه الحزمة — تمامًا كما جرى مع search_service.py.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ---------------------------------------------------------------------------
# نماذج بيانات مبسَّطة (تعكس مخطط 015_cr005_phase1_identity_providers.sql)
# ---------------------------------------------------------------------------

@dataclass
class IdentityProvider:
    code: str
    display_name: str
    category: str  # password | otp | oauth
    is_enabled: bool


@dataclass
class UserIdentity:
    id: str
    user_id: str
    provider_code: str
    external_identifier: str
    is_verified: bool
    is_primary: bool = False
    last_authenticated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# استثناءات منطق الأعمال (تُترجَم لاحقًا لاستجابات نموذج الخطأ الموحّد)
# ---------------------------------------------------------------------------

class ProviderDisabledError(Exception):
    """REQ-IAM-013: محاولة استخدام وسيلة هوية معطَّلة إداريًا."""


class DuplicateIdentityError(Exception):
    """قيد قاعدة البيانات uq_user_identities_provider_identifier منطقيًا."""


class LastIdentityRemovalError(Exception):
    """REQ-IAM-016: منع إزالة آخر وسيلة هوية صالحة متبقية على الحساب."""


# ---------------------------------------------------------------------------
# REQ-IAM-013: التحقق من تفعيل وسيلة الهوية قبل أي استخدام
# ---------------------------------------------------------------------------

def ensure_provider_enabled(providers: List[IdentityProvider], provider_code: str) -> IdentityProvider:
    provider = next((p for p in providers if p.code == provider_code), None)
    if provider is None or not provider.is_enabled:
        raise ProviderDisabledError(f"وسيلة الهوية '{provider_code}' غير متاحة حاليًا للتسجيل.")
    return provider


# ---------------------------------------------------------------------------
# REQ-IAM-014: منع الحسابات المكرَّرة عبر مطابقة الهوية الموثَّقة
# ---------------------------------------------------------------------------

def find_existing_account_by_verified_identifier(
    all_identities: List[UserIdentity], external_identifier: str
) -> Optional[str]:
    """
    يبحث عبر كل وسائل الهوية الموجودة (بصرف النظر عن نوع المزوّد) عن مطابقة
    لنفس المعرّف الخارجي الموثَّق فعلاً؛ إن وُجدت، يعيد user_id صاحب الحساب
    القائم بدلاً من إنشاء حساب جديد.
    """
    for identity in all_identities:
        if identity.is_verified and identity.external_identifier.lower() == external_identifier.lower():
            return identity.user_id
    return None


def resolve_registration(
    all_identities: List[UserIdentity],
    providers: List[IdentityProvider],
    provider_code: str,
    external_identifier: str,
    is_verified: bool,
    new_user_id_factory,
) -> Dict[str, Any]:
    """
    يحدِّد ما إذا كانت عملية تسجيل/دخول جديدة يجب أن:
    - تُربَط بحساب قائم (REQ-IAM-014)، أو
    - تُنشئ حسابًا جديدًا تمامًا.
    new_user_id_factory: دالة بلا معطيات تُولِّد معرّف مستخدم جديدًا (تُمرَّر من الطبقة المستدعية،
                          لأن توليد المعرّف الفعلي مسؤولية قاعدة البيانات لا هذه الدالة).
    """
    ensure_provider_enabled(providers, provider_code)

    existing_user_id = None
    if is_verified:
        existing_user_id = find_existing_account_by_verified_identifier(all_identities, external_identifier)

    if existing_user_id:
        return {"action": "link_to_existing", "user_id": existing_user_id}

    new_user_id = new_user_id_factory()
    return {"action": "create_new", "user_id": new_user_id}


# ---------------------------------------------------------------------------
# REQ-IAM-015, 016: إضافة وإزالة وسائل الهوية لحساب قائم
# ---------------------------------------------------------------------------

def add_identity(
    all_identities: List[UserIdentity],
    providers: List[IdentityProvider],
    user_id: str,
    provider_code: str,
    external_identifier: str,
    is_verified: bool = False,
) -> UserIdentity:
    ensure_provider_enabled(providers, provider_code)

    # يعكس قيد قاعدة البيانات uq_user_identities_provider_identifier
    for identity in all_identities:
        if identity.provider_code == provider_code and identity.external_identifier.lower() == external_identifier.lower():
            if identity.user_id != user_id:
                raise DuplicateIdentityError(
                    "هذه الوسيلة مرتبطة بالفعل بحساب آخر؛ لا يمكن ربطها بأكثر من حساب."
                )
            else:
                raise DuplicateIdentityError("هذه الوسيلة مرتبطة بالفعل بحسابك.")

    new_identity = UserIdentity(
        id=f"identity-{len(all_identities) + 1}",
        user_id=user_id,
        provider_code=provider_code,
        external_identifier=external_identifier,
        is_verified=is_verified,
    )
    return new_identity


def remove_identity(
    all_identities: List[UserIdentity], user_id: str, identity_id: str
) -> List[UserIdentity]:
    """REQ-IAM-016: يمنع فقط أن يصبح الحساب بلا أي وسيلة هوية فعّالة إطلاقًا؛
    لا يمنع إزالة نوع معيّن من الوسائل بذاته. القيد الوحيد: يجب أن تبقى وسيلة
    هوية واحدة على الأقل (من أي نوع كان) بعد الإزالة؛ إن وُجدت وسيلة أخرى متبقية
    من أي نوع، تُسمَح الإزالة بغض النظر عن نوع الوسيلة المُزالة أو المتبقية."""
    user_identities = [i for i in all_identities if i.user_id == user_id]

    if len(user_identities) <= 1:
        raise LastIdentityRemovalError(
            "لا يمكن إزالة آخر وسيلة هوية متبقية على الحساب؛ أضف وسيلة أخرى أولاً."
        )

    target = next((i for i in user_identities if i.id == identity_id), None)
    if target is None:
        raise ValueError("وسيلة الهوية المطلوبة غير موجودة لهذا الحساب.")

    return [i for i in all_identities if i.id != identity_id]


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3: API -> Service -> Repository)
# ---------------------------------------------------------------------------

def register_or_login_via_repository(
    repository,  # AuthRepository؛ لا استيراد مباشر هنا لتفادي اعتمادية دائرية
    provider_code: str,
    external_identifier: str,
    is_verified: bool = False,
) -> Dict[str, Any]:
    """
    نقطة الدخول الفعلية لتسجيل/تسجيل دخول عبر أي وسيلة هوية؛ تستدعي المستودع
    فقط، ولا تُنفِّذ أي استعلام مباشر (التزامًا بسلسلة الاعتماد الطبقية).
    """
    providers = repository.get_enabled_providers()
    ensure_provider_enabled(providers, provider_code)

    existing_identity = repository.find_identity_by_provider_and_identifier(provider_code, external_identifier)
    if existing_identity:
        return {"action": "existing_login", "user_id": existing_identity.user_id, "identity_id": existing_identity.id}

    all_identities = repository.find_all_identities_by_identifier(external_identifier)
    existing_user_id = None
    if is_verified:
        existing_user_id = find_existing_account_by_verified_identifier(all_identities, external_identifier)

    if existing_user_id:
        # ربط بحساب قائم: عملية إدراج واحدة فقط، لا تستوجب معاملة متعددة الخطوات
        new_identity = UserIdentity(
            id="", user_id=existing_user_id, provider_code=provider_code,
            external_identifier=external_identifier, is_verified=is_verified, is_primary=False,
        )
        saved_identity = repository.insert_identity(new_identity)
        return {"action": "link_to_existing", "user_id": existing_user_id, "identity_id": saved_identity.id}

    # توصية المالك: إنشاء المستخدم ووسيلة هويته الأولى ضمن معاملة واحدة ذرّية،
    # لا استدعاءين منفصلين قد يترك فشل أحدهما قاعدة البيانات غير متناسقة
    user_id, saved_identity = repository.create_user_and_primary_identity(
        provider_code, external_identifier, is_verified
    )
    return {"action": "create_new", "user_id": user_id, "identity_id": saved_identity.id}


def add_identity_via_repository(
    repository, user_id: str, provider_code: str, external_identifier: str, is_verified: bool = False
) -> UserIdentity:
    providers = repository.get_enabled_providers()
    existing_for_user = repository.get_identities_for_user(user_id)
    conflicting = repository.find_identity_by_provider_and_identifier(provider_code, external_identifier)
    if conflicting is not None:
        raise DuplicateIdentityError("هذه الوسيلة مرتبطة بالفعل بحساب (نفس المستخدم أو حساب آخر).")

    new_identity = add_identity(existing_for_user, providers, user_id, provider_code, external_identifier, is_verified)
    return repository.insert_identity(new_identity)


class InvalidPasswordCredentialsError(Exception):
    """
    تعديل CR-013 v2 — REQ-SEC-002/006: يُستخدَم لكل حالات فشل تسجيل الدخول
    بكلمة مرور (هوية غير موجودة، كلمة مرور خاطئة، حساب موقوف/محظور، أو مزوّد
    email_password معطَّل إداريًا)، برسالة واحدة موحَّدة دائمًا، عمدًا، حتى لا
    يستطيع طرف خارجي التمييز بين "الحساب غير موجود" و"كلمة المرور خاطئة"
    (يمنع هجمات تعداد الحسابات — Account Enumeration)."""


def login_with_password_via_repository(repository, login_identifier: str, raw_password: str) -> UserIdentity:
    """
    نقطة الدخول الفعلية لتسجيل الدخول بكلمة مرور فقط (email_password)؛
    لا تُنشئ حسابًا جديدًا أبدًا (بخلاف register_or_login_via_repository
    المخصَّصة لمزوّدي OAuth الذين يتحققون من الهوية مسبقًا عبر الطرف الثالث
    قبل وصولنا؛ كلمة المرور تحتاج تحققًا هنا نفسه، فلا يصح افتراض النجاح
    وإنشاء حساب كما في التدفق الآخر).
    """
    try:
        ensure_provider_enabled(repository.get_enabled_providers(), "email_password")
    except ProviderDisabledError:
        raise InvalidPasswordCredentialsError("بيانات الاعتماد غير صحيحة.")

    identity = repository.find_identity_and_verify_password(
        provider_code="email_password", external_identifier=login_identifier, raw_password=raw_password,
    )
    if identity is None:
        raise InvalidPasswordCredentialsError("بيانات الاعتماد غير صحيحة.")
    return identity


def remove_identity_via_repository(repository, user_id: str, identity_id: str) -> None:
    existing_for_user = repository.get_identities_for_user(user_id)
    remove_identity(existing_for_user, user_id, identity_id)  # يتحقق فقط من قاعدة REQ-IAM-016 (يرمي استثناءً إن انتهك)
    repository.delete_identity(identity_id)


# ---------------------------------------------------------------------------
# REQ-IAM-017: بيانات حدث أمني مقترَح لكل عملية إدارة هوية (لا كتابة فعلية هنا)
# ---------------------------------------------------------------------------

def build_security_audit_event(action: str, user_id: str, provider_code: str) -> Dict[str, Any]:
    """
    يبني وصف حدث أمني جاهزًا لتمريره لخدمة AUD الفعلية (aud.events، log_type='security')؛
    هذه الدالة لا تكتب فعليًا لسجل التدقيق — ذلك جزء من طبقة التكامل اللاحقة.

    ملاحظة تصميمية (لطبقة التكامل اللاحقة، لا هذه الدالة): يجب ألا تُفقَد أحداث
    التدقيق الأمني الناتجة عن عمليات إدارة الهوية عند تعذُّر الوصول اللحظي لخدمة
    AUD؛ يُوصى بآلية تسليم موثوقة (كنمط Outbox أو طابور إعادة محاولة) بدلاً من
    إرسال مباشر عرضة للفقد الصامت عند فشل الاتصال.

    TODO (لحزمة تنفيذية لاحقة): تفعيل phone_otp فعليًا عبر مزوّد خارجي لإرسال
    رموز التحقق؛ لا يستوجب ذلك أي تعديل على تصميم قاعدة البيانات الحالي
    (iam.identity_providers/iam.user_identities)، فقط تفعيل الصف الموجود أصلاً
    بحالة is_enabled=false وربط منطق إرسال/التحقق من الرمز.
    """
    allowed_actions = {"identity_added", "identity_removed", "identity_linked", "password_changed", "otp_verified"}
    if action not in allowed_actions:
        raise ValueError(f"نوع حدث غير معروف: {action}")

    return {
        "log_type": "security",
        "event_name": action,
        "actor_ref_id": user_id,
        "metadata": {"provider_code": provider_code},
    }
