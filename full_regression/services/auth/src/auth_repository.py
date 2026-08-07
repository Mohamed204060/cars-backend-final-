"""
auth_repository.py — طبقة الوصول للبيانات لخدمة الهوية والمصادقة (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.3 (معيار Repository الإلزامي + سلسلة الاعتماد
        API -> Service -> Repository)؛ Migration 015/016/017 (CR-005)

نفس بنية search_repository.py تمامًا: واجهة تجريدية (AuthRepository) لا تعرف
عنها auth_service.py شيئًا سوى العقد، تنفيذ فعلي عبر PostgreSQL، وتنفيذ وهمي
في الذاكرة للاختبار دون قاعدة بيانات حقيقية.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
import threading

from auth_service import IdentityProvider, UserIdentity, DuplicateIdentityError


class AuthRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه auth_service.py."""

    @abstractmethod
    def get_enabled_providers(self) -> List[IdentityProvider]:
        raise NotImplementedError

    @abstractmethod
    def find_identity_by_provider_and_identifier(
        self, provider_code: str, external_identifier: str
    ) -> Optional[UserIdentity]:
        raise NotImplementedError

    @abstractmethod
    def find_all_identities_by_identifier(self, external_identifier: str) -> List[UserIdentity]:
        """للبحث عبر جميع المزوّدين (REQ-IAM-014: منع الحسابات المكرَّرة)."""
        raise NotImplementedError

    @abstractmethod
    def get_identities_for_user(self, user_id: str) -> List[UserIdentity]:
        raise NotImplementedError

    @abstractmethod
    def insert_identity(self, identity: UserIdentity) -> UserIdentity:
        raise NotImplementedError

    @abstractmethod
    def delete_identity(self, identity_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_user(self) -> str:
        """ينشئ صفًا جديدًا في iam.users ويُعيد معرّفه؛ يُستخدَم كـnew_user_id_factory."""
        raise NotImplementedError

    @abstractmethod
    def create_user_and_primary_identity(
        self, provider_code: str, external_identifier: str, is_verified: bool
    ):
        """
        توصية المالك: ينفِّذ إنشاء المستخدم وربط وسيلة هويته الأولى معًا ضمن
        معاملة قاعدة بيانات واحدة (Transaction)؛ فشل أي خطوة يُلغي الخطوتين
        معًا، فلا تبقى قاعدة البيانات بحالة غير متناسقة (مستخدم بلا أي وسيلة
        هوية). يُعيد (user_id, UserIdentity)."""
        raise NotImplementedError


class PostgresAuthRepository(AuthRepository):
    """
    تنفيذ فعلي عبر PostgreSQL، يعتمد على مخطط 015_cr005_phase1_identity_providers.sql.
    ملاحظة أمانة: لم يُختبَر على اتصال حي داخل هذه البيئة (لا اتصال شبكي).
    """

    def __init__(self, connection):
        self._connection = connection

    def get_enabled_providers(self) -> List[IdentityProvider]:
        # يعتمد على uq_identity_providers_code (فحص is_enabled مباشرة، لا فهرس إضافي مطلوب لجدول صغير الحجم)
        query = "SELECT code, display_name, provider_category, is_enabled FROM iam.identity_providers WHERE is_enabled = true"
        with self._connection.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        return [IdentityProvider(code=r["code"], display_name=r["display_name"],
                                  category=r["provider_category"], is_enabled=r["is_enabled"]) for r in rows]

    def find_identity_by_provider_and_identifier(
        self, provider_code: str, external_identifier: str
    ) -> Optional[UserIdentity]:
        # يعتمد على uq_user_identities_provider_identifier (فهرس تفرّد يُستخدَم كفهرس بحث أيضًا)
        query = """
            SELECT ui.id, ui.user_id, ip.code AS provider_code, ui.external_identifier,
                   (ui.verified_at IS NOT NULL) AS is_verified, ui.is_primary, ui.last_authenticated_at
            FROM iam.user_identities ui
            JOIN iam.identity_providers ip ON ip.id = ui.provider_type_id
            WHERE ip.code = %(provider_code)s AND lower(ui.external_identifier) = lower(%(external_identifier)s)
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"provider_code": provider_code, "external_identifier": external_identifier})
            row = cur.fetchone()
        return self._row_to_identity(row) if row else None

    def find_all_identities_by_identifier(self, external_identifier: str) -> List[UserIdentity]:
        # فحص عبر جميع المزوّدين لغرض REQ-IAM-014؛ لا فهرس مخصَّص على external_identifier وحده حاليًا
        # (فهرس uq_user_identities_provider_identifier مركَّب مع provider_type_id)؛ ملاحظة أداء محتملة
        # إن كبر حجم الجدول لاحقًا: قد يُفيد فهرس إضافي على external_identifier فقط (YAGNI الآن).
        query = """
            SELECT ui.id, ui.user_id, ip.code AS provider_code, ui.external_identifier,
                   (ui.verified_at IS NOT NULL) AS is_verified, ui.is_primary, ui.last_authenticated_at
            FROM iam.user_identities ui
            JOIN iam.identity_providers ip ON ip.id = ui.provider_type_id
            WHERE lower(ui.external_identifier) = lower(%(external_identifier)s)
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"external_identifier": external_identifier})
            rows = cur.fetchall()
        return [self._row_to_identity(r) for r in rows]

    def get_identities_for_user(self, user_id: str) -> List[UserIdentity]:
        # يعتمد على idx_user_identities_user_id
        query = """
            SELECT ui.id, ui.user_id, ip.code AS provider_code, ui.external_identifier,
                   (ui.verified_at IS NOT NULL) AS is_verified, ui.is_primary, ui.last_authenticated_at
            FROM iam.user_identities ui
            JOIN iam.identity_providers ip ON ip.id = ui.provider_type_id
            WHERE ui.user_id = %(user_id)s
        """
        with self._connection.cursor() as cur:
            cur.execute(query, {"user_id": user_id})
            rows = cur.fetchall()
        return [self._row_to_identity(r) for r in rows]

    def insert_identity(self, identity: UserIdentity) -> UserIdentity:
        """
        سيناريو التزامن (توصية المالك): إذا حاول طلبان متزامنان ربط نفس
        (provider_type_id, external_identifier)، فإن قيد قاعدة البيانات
        uq_user_identities_provider_identifier (015_cr005_phase1_identity_providers.sql)
        يضمن نجاح إدراج واحد فقط على مستوى المحرك نفسه، بصرف النظر عن أي
        فحص تطبيقي سابق قد يتعرَّض لحالة سباق (Race Condition)؛ المحاولة
        الثانية تفشل بخطأ انتهاك تفرّد (UniqueViolation) تُترجَم هنا صراحة
        إلى DuplicateIdentityError بدلاً من تسريب استثناء قاعدة بيانات خام.
        """
        query = """
            INSERT INTO iam.user_identities (user_id, provider_type_id, external_identifier, verified_at, is_primary)
            SELECT %(user_id)s, ip.id, %(external_identifier)s, (CASE WHEN %(is_verified)s THEN now() ELSE NULL END), %(is_primary)s
            FROM iam.identity_providers ip WHERE ip.code = %(provider_code)s
            RETURNING id
        """
        try:
            with self._connection:
                with self._connection.cursor() as cur:
                    cur.execute(query, {
                        "user_id": identity.user_id, "provider_code": identity.provider_code,
                        "external_identifier": identity.external_identifier,
                        "is_verified": identity.is_verified, "is_primary": identity.is_primary,
                    })
                    new_id = cur.fetchone()["id"]
        except Exception as exc:
            # psycopg2.errors.UniqueViolation في التنفيذ الفعلي؛ يُفحَص اسم الاستثناء
            # نصيًا هنا لتفادي استيراد psycopg2 مباشرة في طبقة لم تُختبَر بعد على اتصال حي
            if "UniqueViolation" in type(exc).__name__ or "unique" in str(exc).lower():
                raise DuplicateIdentityError(
                    "هذه الوسيلة أصبحت مرتبطة بحساب آخر في نفس اللحظة (تعارض تزامن)؛ العملية مرفوضة."
                ) from exc
            raise
        identity.id = new_id
        return identity

    def delete_identity(self, identity_id: str) -> None:
        with self._connection.cursor() as cur:
            cur.execute("DELETE FROM iam.user_identities WHERE id = %(id)s", {"id": identity_id})

    def create_user(self) -> str:
        with self._connection.cursor() as cur:
            cur.execute("INSERT INTO iam.users (business_code, primary_role, account_type) "
                        "VALUES (gen_random_uuid()::text, 'individual_buyer', 'individual') RETURNING id")
            return cur.fetchone()["id"]

    def create_user_and_primary_identity(self, provider_code: str, external_identifier: str, is_verified: bool):
        """
        معاملة قاعدة بيانات واحدة تجمع الخطوتين: لا commit ضمني بين
        الاستعلامين؛ يُنفَّذ commit صريح واحد فقط بعد نجاحهما معًا، وrollback
        تلقائي عبر مدير السياق عند أي استثناء يمنع أي حالة وسيطة غير متناسقة.
        """
        try:
            with self._connection:  # يبدأ معاملة ضمنية؛ commit تلقائي عند الخروج بلا استثناء
                with self._connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO iam.users (business_code, primary_role, account_type) "
                        "VALUES (gen_random_uuid()::text, 'individual_buyer', 'individual') RETURNING id"
                    )
                    user_id = cur.fetchone()["id"]

                    cur.execute("""
                        INSERT INTO iam.user_identities (user_id, provider_type_id, external_identifier, verified_at, is_primary)
                        SELECT %(user_id)s, ip.id, %(external_identifier)s,
                               (CASE WHEN %(is_verified)s THEN now() ELSE NULL END), true
                        FROM iam.identity_providers ip WHERE ip.code = %(provider_code)s
                        RETURNING id
                    """, {
                        "user_id": user_id, "provider_code": provider_code,
                        "external_identifier": external_identifier, "is_verified": is_verified,
                    })
                    identity_id = cur.fetchone()["id"]
            # يصل التنفيذ هنا فقط بعد commit ناجح للخطوتين معًا
            identity = UserIdentity(
                id=identity_id, user_id=user_id, provider_code=provider_code,
                external_identifier=external_identifier, is_verified=is_verified, is_primary=True,
            )
            return user_id, identity
        except Exception:
            # rollback يحدث تلقائيًا عبر مدير سياق الاتصال عند خروج غير طبيعي؛
            # إعادة رفع الاستثناء لتتعامل معه الطبقة المستدعية (auth_service.py)
            raise

    @staticmethod
    def _row_to_identity(row) -> UserIdentity:
        return UserIdentity(
            id=row["id"], user_id=row["user_id"], provider_code=row["provider_code"],
            external_identifier=row["external_identifier"], is_verified=row["is_verified"],
            is_primary=row["is_primary"], last_authenticated_at=row["last_authenticated_at"],
        )


class InMemoryAuthRepository(AuthRepository):
    """تنفيذ وهمي للاختبار فقط؛ يحاكي الجداول كقوائم في الذاكرة."""

    def __init__(self, providers: List[IdentityProvider], identities: Optional[List[UserIdentity]] = None):
        self._providers = providers
        self._identities: List[UserIdentity] = identities or []
        self._next_user_seq = 1
        self._next_identity_seq = len(self._identities) + 1
        # قفل يحاكي ضمان التفرّد الذري لقيد قاعدة البيانات الحقيقي
        # (uq_user_identities_provider_identifier) عند تزامن طلبين
        self._lock = threading.Lock()

    def get_enabled_providers(self) -> List[IdentityProvider]:
        return [p for p in self._providers if p.is_enabled]

    def find_identity_by_provider_and_identifier(self, provider_code, external_identifier):
        for identity in self._identities:
            if identity.provider_code == provider_code and identity.external_identifier.lower() == external_identifier.lower():
                return identity
        return None

    def find_all_identities_by_identifier(self, external_identifier):
        return [i for i in self._identities if i.external_identifier.lower() == external_identifier.lower()]

    def get_identities_for_user(self, user_id):
        return [i for i in self._identities if i.user_id == user_id]

    def insert_identity(self, identity: UserIdentity) -> UserIdentity:
        """
        محاكاة سلوك قيد uq_user_identities_provider_identifier الذرّي عبر
        قفل: الفحص والإدراج معًا كوحدة واحدة غير قابلة للمقاطعة، تمامًا كما
        يضمن قيد قاعدة البيانات الحقيقي عند التزامن (لا حالة سباق ممكنة).
        """
        with self._lock:
            for existing in self._identities:
                if (existing.provider_code == identity.provider_code
                        and existing.external_identifier.lower() == identity.external_identifier.lower()):
                    raise DuplicateIdentityError(
                        "هذه الوسيلة أصبحت مرتبطة بحساب آخر في نفس اللحظة (تعارض تزامن)؛ العملية مرفوضة."
                    )
            identity.id = f"identity-{self._next_identity_seq}"
            self._next_identity_seq += 1
            self._identities.append(identity)
            return identity

    def delete_identity(self, identity_id: str) -> None:
        self._identities = [i for i in self._identities if i.id != identity_id]

    def create_user(self) -> str:
        new_id = f"user-{self._next_user_seq}"
        self._next_user_seq += 1
        return new_id

    def create_user_and_primary_identity(self, provider_code: str, external_identifier: str, is_verified: bool):
        """محاكاة الذرّية في الذاكرة: عملية بايثون واحدة غير قابلة للمقاطعة عمليًا لأغراض الاختبار."""
        user_id = self.create_user()
        identity = UserIdentity(
            id=f"identity-{self._next_identity_seq}", user_id=user_id, provider_code=provider_code,
            external_identifier=external_identifier, is_verified=is_verified, is_primary=True,
        )
        self._next_identity_seq += 1
        self._identities.append(identity)
        return user_id, identity
