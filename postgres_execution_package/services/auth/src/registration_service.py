"""
registration_service.py — تنسيق تسجيل حساب عام جديد (CR-018)
المرجع: REQ-IAM-001, REQ-IAM-002, REQ-IAM-006, REQ-STR-001, REQ-SEC-006

هذا الملف هو الوحيد الذي "يعرف" عن IAM وSTR معًا — auth_api.py يبقى طبقة
رقيقة (يستدعي هذه الدالة فقط، لا منطق تنسيق داخله). AuthRepository يكتب في
iam.* حصرًا، StoreRepository يكتب في str.* حصرًا؛ كلاهما لا يزال لا يعرف
الآخر إطلاقًا — فقط طبقة المعاملة (connection) مشترَكة بينهما، لا منطق العمل.

Mapping role_choice/account_type → primary_role (من CHECK constraint الفعلي
في 001_iam.sql، لا قيمة مخترعة):
    buyer  + individual → individual_buyer
    buyer  + business   → business_buyer
    seller + individual → individual_seller
    seller + business   → business_seller
لا قيمة إدارية (admin/moderator/super_admin/news_editor/support_moderator)
قابلة للوصول من هذا المسار إطلاقًا — role_choice تقبل "buyer"/"seller" فقط
بنيويًا (Pydantic Literal في auth_api.py)، فلا حاجة لفحص Blocklist هنا.
"""

from dataclasses import dataclass
from typing import Optional

from password_policy import validate_password_policy
from auth_service import DuplicateIdentityError

SELLER_ROLES = {"individual_seller", "business_seller"}

_ROLE_MAPPING = {
    ("buyer", "individual"): "individual_buyer",
    ("buyer", "business"): "business_buyer",
    ("seller", "individual"): "individual_seller",
    ("seller", "business"): "business_seller",
}


class InvalidRegistrationChoiceError(Exception):
    """role_choice أو account_type خارج القيم المسموحة بنيويًا (دفاعي؛ Pydantic
    Literal في auth_api.py يمنع الوصول هنا أصلًا بقيمة غير صالحة عادةً)."""


@dataclass
class RegistrationResult:
    user_id: str
    primary_role: str
    account_type: str
    store_id: Optional[str]


def register_user(
    auth_repo,
    store_repo,
    create_store_fn,  # حقن create_store النقية من store_service.py — لا استيراد مباشر لتفادي اقتران STR↔IAM بنيويًا
    role_choice: str,
    account_type: str,
    email: str,
    raw_password: str,
) -> RegistrationResult:
    mapping_key = (role_choice, account_type)
    if mapping_key not in _ROLE_MAPPING:
        raise InvalidRegistrationChoiceError(f"تركيبة role_choice/account_type غير صالحة: {mapping_key}")
    primary_role = _ROLE_MAPPING[mapping_key]

    validate_password_policy(raw_password)  # يرفع WeakPasswordError قبل أي كتابة لقاعدة البيانات

    with auth_repo.connection:
        # معاملة واحدة تُغلِّف iam.* وstr.* معًا (نفس كائن الاتصال المشترَك
        # فعليًا بين كل الـRepositories — مؤكَّد من Fixtures التكامل القائمة).
        # فشل أي خطوة (بما فيها إدراج المتجر) يُلغي الاثنتين معًا تلقائيًا.
        user_id, _identity = auth_repo.create_user_and_identity_no_commit(
            primary_role=primary_role, account_type=account_type,
            provider_code="email_password", external_identifier=email, raw_password=raw_password,
        )

        store_id: Optional[str] = None
        if primary_role in SELLER_ROLES:
            # REQ-STR-001: بلا country_ref_id/city_ref_id — الحقل الوحيد
            # الإلزامي فعليًا هو owner_user_ref_id (مؤكَّد من فحص str.stores
            # وStoreCreateRequest وcreate_store()، الاثنان الآخران Optional).
            store = create_store_fn(owner_user_ref_id=user_id)
            store = store_repo.insert_store(store)
            store_id = store.id

    return RegistrationResult(
        user_id=user_id, primary_role=primary_role, account_type=account_type, store_id=store_id,
    )
