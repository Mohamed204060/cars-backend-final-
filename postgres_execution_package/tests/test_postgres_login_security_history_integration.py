"""
test_postgres_login_security_history_integration.py — اختبارات تكامل حقيقية
على PostgreSQL حي لدفعة Admin Operational Completion: Login/Security/Audit
History (Gap Sweep v2.2)، وAccount Status Administration، وPrivate-Message
Administrative Access.

يغطي معايير القبول المعتمَدة صراحةً في المراجعات الأمنية المتتالية:
- login_success/login_failed مع timestamp + IP حقيقيين.
- attempted_identifier_hmac (بلا نص خام، حتمي، قابل للتمييز).
- حماية من انتحال X-Forwarded-For.
- حدود صلاحيات carsmaint_app الفعلية (INSERT ينجح، UPDATE/DELETE يُرفَضان)
  على اتصال حقيقي بهذا الدور، لا افتراضًا من وجود GRANT/REVOKE فقط.
- الوصول الإداري المميَّز لمحتوى الرسائل (بما فيها المحذوفة من الطرفين).

الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا في
هذه البيئة (لا psycopg2/fastapi متاحين هنا، ولا اتصال شبكي)؛ يُنفَّذ فعليًا
عبر GitHub Actions PostgreSQL Validation.
"""

import os
import uuid

import pytest
import psycopg2
import psycopg2.extras
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import PostgresAuthRepository
from session_repository import PostgresSessionRepository
from aud_api import router as aud_router
from aud_repository import PostgresAudRepository
from message_api import router as message_router
from message_repository import PostgresMessageRepository
from order_api import get_order_repository
from order_repository import PostgresOrderRepository
from store_api import router as store_router
from store_repository import PostgresStoreRepository
from inventory_item_api import router as inventory_router
from inventory_item_repository import PostgresInventoryItemRepository
from ref_repository import PostgresRefRepository
from vct_repository import PostgresVctRepository


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")

# سر اختبار فقط — لا علاقة له بأي سر إنتاجي، ولا يُستخدَم إلا داخل هذا الملف.
os.environ.setdefault("LOGIN_IDENTIFIER_HMAC_SECRET", "integration-test-only-hmac-secret-do-not-use-in-prod")

# كلمة مرور اختبار فقط لدور carsmaint_app — تُضبَط هنا (لا في أي Migration
# أو ملف Repository) عبر اتصال Superuser وقت الاختبار فقط، تحديدًا لأن
# 035_login_security_history.sql تعمّدت عدم ضبط أي كلمة مرور (ممنوع صراحةً
# تسجيل كلمات مرور في Migrations/Source Control). بيئة الإنتاج الفعلية
# تضبط كلمة مرور مختلفة تمامًا عبر إدارة أسرارها الخاصة، لا هذه القيمة.
_CARSMAINT_APP_TEST_PASSWORD = "integration-test-only-app-role-password"

# TEST-NET-3 (RFC 5737) — نطاق محجوز رسميًا للتوثيق/الاختبار، غير قابل
# للتوجيه على الإنترنت الحقيقي أبدًا. يُستخدَم كـpeer اصطناعي حتمي وصالح
# لـTestClient بدل الافتراضي غير الرقمي — راجع الشرح الكامل عند بناء
# TestClient أدناه (app_and_client fixture).
TEST_CLIENT_PEER_IP = "203.0.113.42"


@pytest.fixture
def conn():
    connection = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    yield connection
    connection.rollback()
    connection.close()


@pytest.fixture
def app_and_client(conn):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(aud_router)
    app.include_router(message_router)
    app.include_router(store_router)
    app.include_router(inventory_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.aud_repository = PostgresAudRepository(conn)
    app.state.message_repository = PostgresMessageRepository(conn)
    app.state.order_repository = PostgresOrderRepository(conn)
    app.state.store_repository = PostgresStoreRepository(conn)
    app.state.inventory_repository = PostgresInventoryItemRepository(conn)
    app.state.ref_repository = PostgresRefRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)
    # Root Cause (Evidence Gate — Login/Security PostgreSQL integration):
    # Starlette TestClient يضبط peer العميل افتراضيًا إلى tuple اصطناعي غير
    # رقمي (المعرَّف الوثائقي المعروف تاريخيًا لـStarlette: ("testclient", 50000))
    # — request.client.host هذا لا يُحلَّل كـIPv4/IPv6 صالح أبدًا عبر
    # resolve_authoritative_client_ip، فينتج None حتمًا؛ هذا سلوك الإنتاج
    # الصحيح (رفض مضيف غير رقمي)، لا خطأ فيه، ولا يجوز إضعافه. الإصلاح هنا
    # في طبقة الاختبار حصرًا: TestClient نفسها تدعم رسميًا معامل `client=`
    # لضبط peer اصطناعي حقيقي وصالح (IPv4 محجوز للتوثيق/الاختبار فقط، وفق
    # RFC 5737 — TEST-NET-3، غير قابل للتوجيه أبدًا على الإنترنت الحقيقي) —
    # هذا يُشغِّل resolve_authoritative_client_ip الحقيقي كاملًا (لا تجاوز
    # له، لا حقن IP مباشر في منطق الإنتاج) ضد peer صالح وحتمي فعليًا.
    client = TestClient(app, base_url="https://testserver", client=(TEST_CLIENT_PEER_IP, 12345))
    return app, client, conn


def _open_independent_connection():
    """اتصال PostgreSQL مستقل تمامًا عن اتصال الـFixture الأساسي (conn) —
    الدليل الحقيقي الوحيد على الثبات (Durability): رؤية صف على *نفس*
    الاتصال الذي أدرجه لا تُثبِت شيئًا (قد يكون معلَّقًا بلا Commit بعد؛
    PostgreSQL يُظهِر للمعاملة كتابتها غير المُثبَّتة لنفسها دائمًا). فقط
    اتصال آخر تمامًا يمكنه إثبات أن الصف صار مرئيًا خارج معاملة الكاتب —
    أي أنه أُثبِّت (Committed) فعليًا."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _capture_new_events(cur, event_name: str, action_fn):
    """Root Cause (Evidence Gate — Login/Security PostgreSQL integration، بند 2/3):
    عزل حتمي بالمعرِّفات (Before/After ID Diff)، لا ORDER BY ... LIMIT N —
    الأخيرة غير موثوقة على قاعدة اختبار مشتركة/متراكمة (قد تلتقط صفًا من
    اختبار آخر، أو صفوفًا بطوابع زمنية متطابقة/بترتيب غير حتمي). يُنفِّذ
    action_fn() ويُعيد (نتيجتها، فقط الصفوف الجديدة فعليًا لـevent_name هذا
    التي ظهرت أثناء تنفيذها) — بمعزل تام عن أي بيانات موجودة مسبقًا أو
    مُدرَجة من اختبارات أخرى. يعتمد على تنفيذ اختبارات هذا الملف تباعًا
    (Sequential، سلوك pytest الافتراضي بلا تشغيل متوازٍ)."""
    cur.execute("SELECT id FROM aud.events WHERE event_name = %(event_name)s", {"event_name": event_name})
    before_ids = {row["id"] for row in cur.fetchall()}

    result = action_fn()

    cur.execute("SELECT * FROM aud.events WHERE event_name = %(event_name)s", {"event_name": event_name})
    after_rows = cur.fetchall()
    new_rows = [row for row in after_rows if row["id"] not in before_ids]
    return result, new_rows


def _register_and_login(client, conn, email: str, role: str = "individual_buyer") -> str:
    resp = client.post("/api/v1/auth/register", json={
        "role_choice": "buyer" if "buyer" in role else "seller",
        "account_type": "individual", "email": email, "password": "Str0ngPass1!",
    })
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user_id"]
    if role not in ("individual_buyer", "individual_seller"):
        cur = conn.cursor()
        cur.execute("UPDATE iam.users SET primary_role = %s WHERE id = %s", (role, user_id))
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    return user_id


class TestCarsmaintAppPrivilegeBoundary:
    """Gap Sweep v2.2، بند 5-A: إثبات فعلي — لا افتراض من REVOKE ... FROM
    PUBLIC وحدها. اتصال حقيقي كـcarsmaint_app، لا postgres Superuser."""

    @pytest.fixture
    def restricted_conn(self, conn):
        # ضبط كلمة مرور اختبار فقط عبر اتصال Superuser (conn الحالي)، ثم
        # اتصال منفصل تمامًا كـcarsmaint_app لإثبات حدود صلاحياته الفعلية.
        cur = conn.cursor()
        cur.execute("ALTER ROLE carsmaint_app WITH PASSWORD %s", (_CARSMAINT_APP_TEST_PASSWORD,))
        conn.commit()

        restricted_url = DATABASE_URL.replace("postgres:postgres@", f"carsmaint_app:{_CARSMAINT_APP_TEST_PASSWORD}@")
        restricted = psycopg2.connect(restricted_url, cursor_factory=psycopg2.extras.RealDictCursor)
        yield restricted
        restricted.rollback()
        restricted.close()

    def test_insert_succeeds_as_restricted_role(self, restricted_conn):
        cur = restricted_conn.cursor()
        cur.execute(
            "INSERT INTO aud.events (log_type, event_name, correlation_id, metadata) "
            "VALUES ('security', 'login_success', gen_random_uuid(), '{}'::jsonb) RETURNING id",
        )
        row = cur.fetchone()
        restricted_conn.commit()
        assert row is not None and row["id"] is not None

    def test_update_rejected_as_restricted_role(self, conn, restricted_conn):
        # صف حقيقي أُدرج عبر اتصال Superuser (conn) لضمان وجوده بصرف النظر
        # عن نتيجة اختبار INSERT أعلاه (استقلال الاختبارات).
        setup_cur = conn.cursor()
        setup_cur.execute(
            "INSERT INTO aud.events (log_type, event_name, correlation_id, metadata) "
            "VALUES ('security', 'login_success', gen_random_uuid(), '{}'::jsonb) RETURNING id",
        )
        event_id = setup_cur.fetchone()["id"]
        conn.commit()

        cur = restricted_conn.cursor()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("UPDATE aud.events SET reason = 'tampered' WHERE id = %s", (event_id,))

    def test_delete_rejected_as_restricted_role(self, conn, restricted_conn):
        setup_cur = conn.cursor()
        setup_cur.execute(
            "INSERT INTO aud.events (log_type, event_name, correlation_id, metadata) "
            "VALUES ('security', 'login_success', gen_random_uuid(), '{}'::jsonb) RETURNING id",
        )
        event_id = setup_cur.fetchone()["id"]
        conn.commit()

        cur = restricted_conn.cursor()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM aud.events WHERE id = %s", (event_id,))

    def test_negative_control_postgres_role_can_update_and_delete(self, conn):
        """ضبط الاختبار: يثبت أن الاختبارين أعلاه يفحصان فعليًا حدود
        الدور، لا شيئًا آخر — نفس العمليتين تنجحان بلا قيد عبر postgres."""
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO aud.events (log_type, event_name, correlation_id, metadata) "
            "VALUES ('security', 'login_success', gen_random_uuid(), '{}'::jsonb) RETURNING id",
        )
        event_id = cur.fetchone()["id"]
        cur.execute("UPDATE aud.events SET reason = 'control-update' WHERE id = %s", (event_id,))
        cur.execute("DELETE FROM aud.events WHERE id = %s", (event_id,))
        # لا استثناء يعني نجاح كلتا العمليتين — الفرق مقابل restricted_conn إثبات الحدود الفعلية.


class TestSuccessfulLoginHistory:

    def test_login_creates_one_historical_security_event(self, app_and_client):
        """تصحيح ثبات: الإثبات عبر اتصال PostgreSQL مستقل تمامًا — نفس مبدأ
        login_failed. المسار مغلَّف فعليًا بـwith auth_repo.connection: في
        auth_api.py، فالإثبات هنا يتحقق من ذلك فعليًا لا افتراضًا."""
        app, client, conn = app_and_client
        email = f"loginhist-{uuid.uuid4().hex[:8]}@example.com"
        user_id = _register_and_login(client, conn, email)
        client.post("/api/v1/auth/logout")

        independent_conn = _open_independent_connection()
        try:
            icur = independent_conn.cursor()
            icur.execute(
                "SELECT * FROM aud.events WHERE event_name = 'login_success' AND actor_ref_id = %s",
                (user_id,),
            )
            rows = icur.fetchall()
        finally:
            independent_conn.close()

        # تسجيل واحد وقت التسجيل (register يُنشئ جلسة فعلية أيضًا) —
        # نتحقق من الحد الأدنى: صف واحد على الأقل حقيقي وصحيح ومُثبَّت.
        assert len(rows) >= 1
        row = rows[0]
        assert row["log_type"] == "security"
        assert row["occurred_at_utc"] is not None
        assert row["metadata"].get("ip_address") == TEST_CLIENT_PEER_IP

    def test_explicit_login_after_logout_creates_additional_event(self, app_and_client):
        app, client, conn = app_and_client
        email = f"relogin-{uuid.uuid4().hex[:8]}@example.com"
        user_id = _register_and_login(client, conn, email)
        client.post("/api/v1/auth/logout")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM aud.events WHERE event_name = 'login_success' AND actor_ref_id = %s", (user_id,))
        count_before = cur.fetchone()["c"]

        resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
        assert resp.status_code == 200

        cur.execute("SELECT COUNT(*) AS c FROM aud.events WHERE event_name = 'login_success' AND actor_ref_id = %s", (user_id,))
        count_after = cur.fetchone()["c"]
        assert count_after == count_before + 1

    def test_logout_does_not_erase_or_alter_login_history(self, app_and_client):
        app, client, conn = app_and_client
        email = f"logout-preserve-{uuid.uuid4().hex[:8]}@example.com"
        user_id = _register_and_login(client, conn, email)

        cur = conn.cursor()
        cur.execute("SELECT id, occurred_at_utc FROM aud.events WHERE event_name = 'login_success' AND actor_ref_id = %s", (user_id,))
        before = cur.fetchall()
        assert len(before) >= 1

        client.post("/api/v1/auth/logout")

        cur.execute("SELECT id, occurred_at_utc FROM aud.events WHERE event_name = 'login_success' AND actor_ref_id = %s", (user_id,))
        after = cur.fetchall()
        assert {r["id"] for r in before} <= {r["id"] for r in after}
        for b in before:
            match = next(a for a in after if a["id"] == b["id"])
            assert match["occurred_at_utc"] == b["occurred_at_utc"]

    def test_authorized_admin_can_retrieve_login_history(self, app_and_client):
        app, client, conn = app_and_client
        email = f"retrieve-{uuid.uuid4().hex[:8]}@example.com"
        user_id = _register_and_login(client, conn, email)
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"event_name": "login_success", "actor_ref_id": user_id})
        assert resp.status_code == 200, resp.text
        assert resp.json()["pagination"]["total_items"] >= 1

    def test_unauthorized_user_gets_403(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"unauth-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 403


class TestFailedLoginHistory:

    def test_failed_login_creates_durable_event_with_timestamp_and_ip(self, app_and_client):
        """تصحيح ثبات نهائي: الإثبات عبر اتصال PostgreSQL مستقل تمامًا عن
        اتصال الكاتب — رؤية الصف على نفس اتصال الفحص السابق لا تُثبِت شيئًا
        (PostgreSQL يُظهِر للمعاملة كتابتها غير المُثبَّتة لنفسها دائمًا).
        هذا الاختبار يُثبِت أن login_failed يُثبَّت (Commit) فعليًا قبل
        عودة 401 للعميل، لا مجرد مرئي على اتصال التطبيق الداخلي."""
        app, client, conn = app_and_client
        cur = conn.cursor()
        cur.execute("SELECT id FROM aud.events WHERE event_name = 'login_failed'")
        before_ids = {row["id"] for row in cur.fetchall()}

        resp = client.post("/api/v1/auth/login", json={
            "login_identifier": f"nonexistent-{uuid.uuid4().hex[:8]}@example.com", "password": "wrong",
        })
        assert resp.status_code == 401

        independent_conn = _open_independent_connection()
        try:
            icur = independent_conn.cursor()
            icur.execute("SELECT * FROM aud.events WHERE event_name = 'login_failed'")
            visible_from_independent_connection = icur.fetchall()
        finally:
            independent_conn.close()

        new_rows = [row for row in visible_from_independent_connection if row["id"] not in before_ids]
        assert len(new_rows) == 1, (
            "الحدث يجب أن يكون مرئيًا ومُثبَّتًا فعليًا من اتصال مستقل تمامًا عن الكاتب — "
            "لا مجرد موجود على اتصال التطبيق الداخلي."
        )
        row = new_rows[0]
        assert row["occurred_at_utc"] is not None
        assert row["metadata"].get("ip_address") == TEST_CLIENT_PEER_IP
        assert row["actor_ref_id"] is None

    def test_attempted_identifier_hmac_present_deterministic_and_distinct(self, app_and_client):
        app, client, conn = app_and_client
        cur = conn.cursor()
        identifier_a = f"hmac-a-{uuid.uuid4().hex[:8]}@example.com"
        identifier_b = f"hmac-b-{uuid.uuid4().hex[:8]}@example.com"

        def _durable_login_failed_row(identifier, password):
            cur.execute("SELECT id FROM aud.events WHERE event_name = 'login_failed'")
            before_ids = {row["id"] for row in cur.fetchall()}
            resp = client.post("/api/v1/auth/login", json={"login_identifier": identifier, "password": password})
            assert resp.status_code == 401
            independent_conn = _open_independent_connection()
            try:
                icur = independent_conn.cursor()
                icur.execute("SELECT * FROM aud.events WHERE event_name = 'login_failed'")
                visible = icur.fetchall()
            finally:
                independent_conn.close()
            new_rows = [row for row in visible if row["id"] not in before_ids]
            assert len(new_rows) == 1
            return new_rows[0]

        # كل طلب مُلتقَط ومُثبَت الثبات (Durability) على حدة عبر اتصال مستقل
        # — لا اعتماد على ترتيب/عدد الصفوف الكلي عبر الطلبات الثلاثة معًا.
        row_a1 = _durable_login_failed_row(identifier_a, "wrong1")
        hmac_a1 = row_a1["metadata"].get("attempted_identifier_hmac")

        row_a2 = _durable_login_failed_row(identifier_a.upper() + "  ", "wrong2")
        hmac_a2 = row_a2["metadata"].get("attempted_identifier_hmac")

        row_b = _durable_login_failed_row(identifier_b, "wrong3")
        hmac_b = row_b["metadata"].get("attempted_identifier_hmac")

        assert hmac_a1 is not None and hmac_a2 is not None and hmac_b is not None
        assert hmac_a1 == hmac_a2, "نفس المعرِّف بعد التطبيع (حالة أحرف/مسافات) يجب أن ينتج نفس HMAC"
        assert hmac_a1 != hmac_b, "معرِّف مختلف يجب أن ينتج HMAC مختلفًا"

    def test_no_raw_identifier_or_credential_material_anywhere(self, app_and_client):
        app, client, conn = app_and_client
        cur = conn.cursor()
        identifier = f"secret-check-{uuid.uuid4().hex[:8]}@example.com"
        secret_password = "SuperSecretPass1!"

        cur.execute("SELECT id FROM aud.events WHERE event_name = 'login_failed'")
        before_ids = {row["id"] for row in cur.fetchall()}

        resp = client.post("/api/v1/auth/login", json={"login_identifier": identifier, "password": secret_password})
        assert resp.status_code == 401

        independent_conn = _open_independent_connection()
        try:
            icur = independent_conn.cursor()
            icur.execute("SELECT * FROM aud.events WHERE event_name = 'login_failed'")
            visible = icur.fetchall()
        finally:
            independent_conn.close()

        new_rows = [row for row in visible if row["id"] not in before_ids]
        assert len(new_rows) == 1
        metadata_str = str(new_rows[0]["metadata"])
        assert identifier not in metadata_str
        assert secret_password not in metadata_str
        assert "password" not in metadata_str.lower()

    def test_authorized_admin_can_retrieve_failed_login_event(self, app_and_client):
        app, client, conn = app_and_client
        client.post("/api/v1/auth/login", json={
            "login_identifier": f"retrieve-failed-{uuid.uuid4().hex[:8]}@example.com", "password": "wrong",
        })
        _register_and_login(client, conn, f"admin2-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"event_name": "login_failed"})
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] >= 1

    def test_failed_login_returns_503_not_401_when_audit_persistence_fails_at_real_sql_layer(self, app_and_client):
        """الاختبار السلبي المطلوب صراحةً: فشل حقيقي على مستوى PostgreSQL
        نفسه (لا Python RuntimeError مُصطنَع) لإدراج login_failed — يجب أن
        تعود 503 لا 401 بلا دليل تاريخي مُثبَّت. نفس أسلوب الإثبات المستخدَم
        سابقًا لمسار النجاح (عمود غير موجود فعليًا → UndefinedColumn حقيقي)."""
        app, client, conn = app_and_client
        real_aud_repo = app.state.aud_repository
        real_connection = real_aud_repo.connection

        class _GenuinelyBrokenAudRepository:
            connection = real_connection

            def insert_event(self, event):
                with real_connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO aud.events (log_type, event_name, correlation_id, this_column_does_not_exist) "
                        "VALUES (%s, %s, gen_random_uuid(), %s)",
                        ("security", "login_failed", "x"),
                    )

        app.state.aud_repository = _GenuinelyBrokenAudRepository()

        resp = client.post("/api/v1/auth/login", json={
            "login_identifier": f"audit-fail-{uuid.uuid4().hex[:8]}@example.com", "password": "wrong",
        })
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "LOGIN_HISTORY_PERSISTENCE_FAILED"

        # تصحيح أمني مُلزَم: لا تسريب معلومات داخلية في الاستجابة العامة —
        # لا نص استثناء PostgreSQL الخام، لا اسم العمود غير الموجود المُستخدَم
        # عمدًا لإثارة الفشل، لا أي شذرة SQL/تفاصيل قاعدة بيانات.
        response_text = resp.text
        assert "this_column_does_not_exist" not in response_text
        assert "UndefinedColumn" not in response_text
        assert "column" not in response_text.lower()
        assert "INSERT INTO" not in response_text
        assert "psycopg2" not in response_text.lower()

        app.state.aud_repository = real_aud_repo

        # الاتصال يجب أن يكون قابلًا للاستخدام الآن (Rollback حقيقي أخرج
        # المعاملة من حالة Aborted) — إثبات مباشر باستعلام تالٍ ناجح.
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone() is not None


class TestSpoofedForwardedForProtection:
    """Gap Sweep v2.2، بند 4/9: عميل غير موثوق (لا وسيط موثوق مضبوط في هذه
    البيئة، TRUSTED_PROXY_CIDRS فارغ افتراضيًا) لا يستطيع انتحال IP عبر
    X-Forwarded-For."""

    def test_spoofed_header_does_not_become_authoritative_ip(self, app_and_client, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)
        app, client, conn = app_and_client
        email = f"spoof-{uuid.uuid4().hex[:8]}@example.com"

        spoofed_ip = "6.6.6.6"
        resp = client.post(
            "/api/v1/auth/register",
            json={"role_choice": "buyer", "account_type": "individual", "email": email, "password": "Str0ngPass1!"},
            headers={"X-Forwarded-For": spoofed_ip},
        )
        assert resp.status_code == 201
        user_id = resp.json()["user_id"]

        cur = conn.cursor()
        cur.execute(
            "SELECT metadata FROM aud.events WHERE event_name = 'login_success' AND actor_ref_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        recorded_ip = row["metadata"].get("ip_address")
        # الآن مع peer حتمي معروف (TEST_CLIENT_PEER_IP)، تأكيد دقيق كامل لا
        # مجرد "ليس القيمة المُنتحَلة": القيمة المسجَّلة هي peer الحقيقي
        # بالضبط — إثبات إيجابي للسلوك الصحيح، لا سلبي فقط.
        assert recorded_ip == TEST_CLIENT_PEER_IP
        assert recorded_ip != spoofed_ip


class TestAccountStatusAdministrationOnLivePostgres:

    def test_admin_can_suspend_account_and_status_persists(self, app_and_client):
        app, client, conn = app_and_client
        target_email = f"suspend-target-{uuid.uuid4().hex[:8]}@example.com"
        target_id = _register_and_login(client, conn, target_email)
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin3-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        resp = client.post(f"/api/v1/auth/users/{target_id}/status", json={"status": "suspended"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"

        cur = conn.cursor()
        cur.execute("SELECT status FROM iam.users WHERE id = %s", (target_id,))
        assert cur.fetchone()["status"] == "suspended"

    def test_nonexistent_user_returns_404(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin4-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        resp = client.post(f"/api/v1/auth/users/{uuid.uuid4()}/status", json={"status": "suspended"})
        assert resp.status_code == 404


class TestPrivateMessageAdministrativeAccess:

    def test_deleted_by_both_parties_message_remains_readable_by_admin(self, app_and_client):
        """Root Cause (تصحيح سابق كان غير مُثبَت): الاختبار القديم أنشأ
        مُرسِلًا وحيدًا مسجَّلًا فقط، فحذفه الوحيد يُنتِج is_deleted_by_sender=True
        حصرًا — لا مسار عام كان يصل فعليًا لـis_deleted_by_recipient=True معه
        (context_ref_id عشوائي لا يُشير لأي pur.purchase_requests حقيقي، فلا
        مُشارك ثانٍ يُزرَع تلقائيًا إطلاقًا).

        الإصلاح: طلب شراء حقيقي عبر Fixture SQL متحكَّم به (buyer_user_ref_id
        حقيقي؛ pur.purchase_requests بلا FK فعلي لـcatalog_part_ref_id/
        trim_ref_id — راجع 010_pur.sql، فقيم عشوائية آمنة تمامًا هنا) —
        يُتيح ذلك لـget_or_create_conversation_via_repository زرع المشتري
        كمُشارك قانوني تلقائيًا (resolve_canonical_participant لسياق
        purchase_request)، والبائع (المُرسِل الفعلي) يُسجَّل عبر
        send_message_via_repository — طرفان حقيقيان مسجَّلان فعليًا، لا
        طرف وحيد. كل طرف يحذف من منظوره الخاص فعليًا (لا محاكاة)."""
        app, client, conn = app_and_client
        buyer_email = f"buyer-{uuid.uuid4().hex[:8]}@example.com"
        buyer_id = _register_and_login(client, conn, buyer_email)
        client.post("/api/v1/auth/logout")

        pr_id = str(uuid.uuid4())
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pur.purchase_requests (id, business_code, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (pr_id, f"PR-TEST-{uuid.uuid4().hex[:10]}", buyer_id, str(uuid.uuid4()), str(uuid.uuid4())),
        )
        conn.commit()

        seller_email = f"seller-{uuid.uuid4().hex[:8]}@example.com"
        seller_id = _register_and_login(client, conn, seller_email, role="individual_seller")

        secret_body = "محتوى حسّاس يجب أن يبقى قابلًا للاطلاع الإداري رغم الحذف من الطرفين"
        send_resp = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": pr_id, "body": secret_body,
        })
        assert send_resp.status_code == 201, send_resp.text
        message = send_resp.json()
        conversation_id, message_id = message["conversation_id"], message["id"]

        # إثبات مسبق: كلا الطرفين مُشارِكان فعليًا قبل أي حذف (المشتري
        # زُرِع تلقائيًا كمُشارك قانوني، البائع كمُرسِل فعلي).
        cur.execute(
            "SELECT COUNT(*) AS c FROM com.conversation_participants WHERE conversation_id = %s AND user_ref_id IN (%s, %s)",
            (conversation_id, buyer_id, seller_id),
        )
        assert cur.fetchone()["c"] == 2, "المشتري والبائع يجب أن يكونا مُشارِكَين مسجَّلَين فعليًا كلاهما"

        # 1) البائع (المُرسِل الفعلي) يحذف من منظوره
        del_resp_sender = client.delete(f"/api/v1/conversations/{conversation_id}/messages/{message_id}")
        assert del_resp_sender.status_code == 200
        assert del_resp_sender.json()["is_deleted_by_sender"] is True

        # 2) المشتري (طرف حقيقي آخر، ليس المُرسِل) يحذف من منظوره
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": "Str0ngPass1!"})
        del_resp_recipient = client.delete(f"/api/v1/conversations/{conversation_id}/messages/{message_id}")
        assert del_resp_recipient.status_code == 200
        assert del_resp_recipient.json()["is_deleted_by_recipient"] is True

        # إثبات صريح إلزامي قبل القراءة الإدارية: كلا العلمين True فعليًا
        # في القاعدة نفسها، لا افتراضًا من نجاح استدعاءات الحذف فقط.
        cur.execute(
            "SELECT is_deleted_by_sender, is_deleted_by_recipient FROM com.messages WHERE id = %s",
            (message_id,),
        )
        flags = cur.fetchone()
        assert flags["is_deleted_by_sender"] is True
        assert flags["is_deleted_by_recipient"] is True

        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"root-msg-{uuid.uuid4().hex[:8]}@example.com", role="super_admin")

        admin_resp = client.get(f"/api/v1/admin/conversations/{conversation_id}/messages")
        assert admin_resp.status_code == 200, admin_resp.text
        bodies = [m["body"] for m in admin_resp.json()]
        assert secret_body in bodies, "المحتوى يجب أن يبقى مقروءًا إداريًا رغم الحذف من الطرفين معًا"

    def test_insufficient_admin_role_gets_403(self, app_and_client):
        app, client, conn = app_and_client
        conversation_id = str(uuid.uuid4())
        _register_and_login(client, conn, f"regular-admin-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        resp = client.get(f"/api/v1/admin/conversations/{conversation_id}/messages")
        assert resp.status_code == 403

    def test_privileged_read_creates_its_own_durable_audit_record_without_body_content(self, app_and_client):
        """تصحيح ثبات: نفس مبدأ login_failed — الإثبات عبر اتصال PostgreSQL
        مستقل تمامًا عن الكاتب، لا مجرد رؤية على اتصال التطبيق الداخلي."""
        app, client, conn = app_and_client
        sender_id = _register_and_login(client, conn, f"sender2-{uuid.uuid4().hex[:8]}@example.com")
        secret_body = "نص سرّي آخر لا يجب أن يظهر في سجل التدقيق نفسه"
        send_resp = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": str(uuid.uuid4()), "body": secret_body,
        })
        conversation_id = send_resp.json()["conversation_id"]
        client.post("/api/v1/auth/logout")

        admin_id = _register_and_login(client, conn, f"root-msg2-{uuid.uuid4().hex[:8]}@example.com", role="super_admin")
        resp = client.get(f"/api/v1/admin/conversations/{conversation_id}/messages")
        assert resp.status_code == 200

        # الإثبات الحاسم: اتصال مستقل تمامًا — actor_ref_id مُقيَّد بمعرِّف
        # هذا المدير الفريد المُولَّد لهذا الاختبار تحديدًا، فلا صف من أي
        # اختبار آخر يمكن أن يطابقه إطلاقًا؛ لا ORDER BY ... LIMIT.
        independent_conn = _open_independent_connection()
        try:
            icur = independent_conn.cursor()
            icur.execute(
                "SELECT * FROM aud.events WHERE event_name = 'admin_message_content_accessed' "
                "AND actor_ref_id = %s",
                (admin_id,),
            )
            rows = icur.fetchall()
        finally:
            independent_conn.close()

        assert len(rows) == 1, "الحدث يجب أن يكون مرئيًا ومُثبَّتًا فعليًا من اتصال مستقل تمامًا عن الكاتب"
        row = rows[0]
        assert row["log_type"] == "administrative"
        assert row["metadata"].get("conversation_id") == conversation_id
        assert secret_body not in str(row["metadata"])
        assert secret_body not in str(row["before_value"])
        assert secret_body not in str(row["after_value"])

    def test_privileged_read_returns_503_when_audit_persistence_fails_at_real_sql_layer(self, app_and_client):
        """الاختبار السلبي المطلوب: فشل حقيقي على مستوى PostgreSQL لإدراج
        admin_message_content_accessed — يجب ألا يُعاد أي محتوى حسّاس بلا
        دليل تدقيق مُثبَّت فعليًا لهذا الوصول تحديدًا."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"sender3-{uuid.uuid4().hex[:8]}@example.com")
        send_resp = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": str(uuid.uuid4()), "body": "أي محتوى",
        })
        conversation_id = send_resp.json()["conversation_id"]
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"root-msg3-{uuid.uuid4().hex[:8]}@example.com", role="super_admin")

        real_aud_repo = app.state.aud_repository
        real_connection = real_aud_repo.connection

        class _GenuinelyBrokenAudRepository:
            connection = real_connection

            def insert_event(self, event):
                with real_connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO aud.events (log_type, event_name, correlation_id, this_column_does_not_exist) "
                        "VALUES (%s, %s, gen_random_uuid(), %s)",
                        ("administrative", "admin_message_content_accessed", "x"),
                    )

        app.state.aud_repository = _GenuinelyBrokenAudRepository()

        resp = client.get(f"/api/v1/admin/conversations/{conversation_id}/messages")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "MESSAGE_ACCESS_AUDIT_PERSISTENCE_FAILED"
        assert "أي محتوى" not in resp.text

        # تصحيح أمني مُلزَم: نفس فحوصات التسريب المطبَّقة على مسار الدخول
        # الفاشل — لا نص استثناء PostgreSQL خام، لا اسم العمود المُستخدَم
        # عمدًا لإثارة الفشل، لا أي شذرة SQL/تفاصيل قاعدة بيانات.
        response_text = resp.text
        assert "this_column_does_not_exist" not in response_text
        assert "UndefinedColumn" not in response_text
        assert "column" not in response_text.lower()
        assert "INSERT INTO" not in response_text
        assert "psycopg2" not in response_text.lower()

        app.state.aud_repository = real_aud_repo


class TestLoginRegisterTransactionalRollbackOnLivePostgres:
    """تصحيح أمني نهائي: التصميم النهائي يُغلِّف إنشاء الجلسة + تسجيل الحدث
    الأمني الإلزامي داخل معاملة صريحة واحدة (auth_repo.connection) — فشل
    التدقيق يُسقِط المعاملة بالكامل (Rollback حقيقي)، فصف الجلسة لا يُثبَّت
    في iam.sessions أصلًا. لا Compensation منفصلة، فلا احتمال لفشلها بدورها
    على معاملة "Aborted" — هذا مُثبَت هنا مباشرة على PostgreSQL حي."""

    def test_login_session_never_committed_in_real_postgres_on_audit_failure(self, app_and_client):
        app, client, conn = app_and_client
        email = f"pg-login-crash-{uuid.uuid4().hex[:8]}@example.com"
        user_id = _register_and_login(client, conn, email)
        client.post("/api/v1/auth/logout")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM iam.sessions WHERE user_id = %s", (user_id,))
        sessions_before = cur.fetchone()["c"]

        original_insert = app.state.aud_repository.insert_event

        def _broken_insert(event):
            raise RuntimeError("simulated aud outage")
        app.state.aud_repository.insert_event = _broken_insert

        resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "LOGIN_HISTORY_PERSISTENCE_FAILED"
        assert "session_id" not in resp.cookies

        app.state.aud_repository.insert_event = original_insert

        # الإثبات الحاسم: عدد جلسات هذا المستخدم في iam.sessions الحقيقي لم
        # يتغيَّر إطلاقًا — لا صف جديد أصلًا (Rollback حقيقي)، لا صف موجود
        # بحالة "مُبطَلة" (ذلك كان التصميم السابق المرفوض). "لا جلسة نشطة
        # قابلة للاستخدام" مضمون لأن لا جلسة إطلاقًا نتجت عن هذه المحاولة.
        cur.execute("SELECT COUNT(*) AS c FROM iam.sessions WHERE user_id = %s", (user_id,))
        sessions_after = cur.fetchone()["c"]
        assert sessions_after == sessions_before, (
            "فشل التدقيق يجب ألا يُنتِج أي صف جلسة جديد دائم — المعاملة "
            "الصريحة يجب أن تُلغي إنشاء الجلسة بالكامل عبر Rollback حقيقي."
        )

    def test_audit_insert_fails_at_real_postgresql_layer_not_python_runtime_error(self, app_and_client):
        """المتطلب الصريح الأخير: فشل حقيقي على مستوى PostgreSQL نفسه (خطأ
        SQL فعلي من محرِّك قاعدة البيانات)، لا استثناء Python مُحاكًى فقط.
        نستبدل aud_repo.insert_event باستعلام SQL حقيقي يُرسَل فعليًا إلى
        PostgreSQL لكنه يشير لعمود غير موجود (nonexistent_column) — يفشل
        بخطأ PostgreSQL أصيل (UndefinedColumn)، لا RuntimeError مُصطنَع. هذا
        يُدخِل المعاملة الحالية في حالة Aborted فعليًا على مستوى القاعدة —
        الإثبات هنا أن with-block نفسه ينفِّذ Rollback حقيقيًا يُخرِج
        الاتصال من هذه الحالة بأمان (لا محاولة تالية على معاملة Aborted)."""
        app, client, conn = app_and_client
        email = f"pg-real-sql-fail-{uuid.uuid4().hex[:8]}@example.com"

        real_aud_repo = app.state.aud_repository
        real_connection = real_aud_repo.connection

        class _GenuinelyBrokenAudRepository:
            """ليست محاكاة بايثون خالصة — تُنفِّذ عبارة SQL حقيقية على نفس
            الاتصال المشترَك فعليًا، تفشل بخطأ PostgreSQL أصيل."""

            def insert_event(self, event):
                with real_connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO aud.events (log_type, event_name, correlation_id, this_column_does_not_exist) "
                        "VALUES (%s, %s, gen_random_uuid(), %s)",
                        ("security", "login_success", "x"),
                    )

        app.state.aud_repository = _GenuinelyBrokenAudRepository()

        resp = client.post("/api/v1/auth/register", json={
            "role_choice": "buyer", "account_type": "individual", "email": email, "password": "Str0ngPass1!",
        })
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "REGISTRATION_SUCCEEDED_SESSION_FAILED"
        assert "session_id" not in resp.cookies

        app.state.aud_repository = real_aud_repo

        # الاتصال يجب أن يكون قابلًا للاستخدام الآن (لا معاملة Aborted
        # متروكة خلفها) — إثبات مباشر: استعلام تالٍ حقيقي ينجح بلا خطأ.
        cur = conn.cursor()
        cur.execute(
            "SELECT u.id FROM iam.users u JOIN iam.user_identities ui ON ui.user_id = u.id "
            "WHERE ui.external_identifier = %s",
            (email,),
        )
        user_row = cur.fetchone()
        assert user_row is not None, "الحساب يجب أن يكون موجودًا فعليًا في iam.users رغم فشل SQL حقيقي للتدقيق"

        # لا صف جلسة دائم نتج عن هذه المحاولة تحديدًا (Rollback حقيقي شمل
        # كل ما كان معلَّقًا على نفس الاتصال وقت الفشل، بما فيها الجلسة)
        cur.execute("SELECT COUNT(*) AS c FROM iam.sessions WHERE user_id = %s", (user_row["id"],))
        assert cur.fetchone()["c"] == 0

        # الحساب لا يزال قابلًا لتسجيل الدخول عبر /login مباشرة (الاتصال
        # سليم تمامًا الآن، لا حالة Aborted متبقية تمنع عمليات لاحقة)
        login_resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
        assert login_resp.status_code == 200

    def test_registration_account_persists_when_session_transaction_rolls_back(self, app_and_client):
        app, client, conn = app_and_client
        email = f"pg-reg-crash-{uuid.uuid4().hex[:8]}@example.com"

        original_insert = app.state.aud_repository.insert_event

        def _broken_insert(event):
            raise RuntimeError("simulated aud outage")
        app.state.aud_repository.insert_event = _broken_insert

        resp = client.post("/api/v1/auth/register", json={
            "role_choice": "buyer", "account_type": "individual", "email": email, "password": "Str0ngPass1!",
        })
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "REGISTRATION_SUCCEEDED_SESSION_FAILED"
        assert "session_id" not in resp.cookies

        app.state.aud_repository.insert_event = original_insert

        # 1) الحساب موجود فعلًا في iam.users رغم استجابة 503 — لا رجوع عنه
        cur = conn.cursor()
        cur.execute(
            "SELECT u.id FROM iam.users u JOIN iam.user_identities ui ON ui.user_id = u.id "
            "WHERE ui.external_identifier = %s",
            (email,),
        )
        user_row = cur.fetchone()
        assert user_row is not None, "الحساب يجب أن يكون موجودًا فعليًا في iam.users رغم 503"

        # 2) لا صف جلسة دائم نتج عن هذه المحاولة الفاشلة (Rollback حقيقي)
        cur.execute("SELECT COUNT(*) AS c FROM iam.sessions WHERE user_id = %s", (user_row["id"],))
        assert cur.fetchone()["c"] == 0

        # 3) إعادة محاولة /register بنفس البريد تصطدم بـ409 — لا حالة غامضة
        retry_resp = client.post("/api/v1/auth/register", json={
            "role_choice": "buyer", "account_type": "individual", "email": email, "password": "Str0ngPass1!",
        })
        assert retry_resp.status_code == 409

class TestTrustedProxyMalformedInputOnLivePostgres:
    """تصحيح أمني نهائي: سلوك الفشل الآمن الصريح لبيانات حدود ثقة فاسدة،
    مُثبَت عبر مسار HTTP حقيقي على PostgreSQL حي، لا اختبار وحدة معزول فقط."""

    def test_malformed_xff_behind_trusted_proxy_yields_no_ip_not_a_guess(self, app_and_client, monkeypatch):
        """عند وسيط موثوق فعليًا، سلسلة X-Forwarded-For الفاسدة يجب ألا
        تُنتِج أي IP مخمَّن — الحدث يُسجَّل بلا ip_address (None)، لا بقيمة
        قد تكون خاطئة. هذا يتطلب TestClient نفسه (Peer) أن يكون ضمن
        النطاق الموثوق — نضبط TRUSTED_PROXY_CIDRS واسعًا بما يكفي ليشمل أي
        عنوان محلي مرجَّح لبيئة الاختبار (127.0.0.0/8 وtestclient الشائع)."""
        monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "0.0.0.0/0")  # يشمل أي Peer فعليًا لبيئة الاختبار هذه تحديدًا
        app, client, conn = app_and_client
        email = f"pg-malformed-xff-{uuid.uuid4().hex[:8]}@example.com"

        resp = client.post(
            "/api/v1/auth/register",
            json={"role_choice": "buyer", "account_type": "individual", "email": email, "password": "Str0ngPass1!"},
            headers={"X-Forwarded-For": "not-an-ip, also-not-an-ip"},
        )
        assert resp.status_code == 201
        user_id = resp.json()["user_id"]

        cur = conn.cursor()
        cur.execute(
            "SELECT metadata FROM aud.events WHERE event_name = 'login_success' AND actor_ref_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        # None، لا أي IP مخمَّن من سلسلة فاسدة — الفشل الآمن الصريح
        assert row["metadata"].get("ip_address") is None
