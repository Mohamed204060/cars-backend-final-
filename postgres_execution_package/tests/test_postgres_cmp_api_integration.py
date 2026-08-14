"""
test_postgres_cmp_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة CMP على PostgreSQL حي (CMP Contract Extension)
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا
بعد؛ لا اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه الحزمة.
"""

import os
import threading
import uuid

import pytest
import psycopg2
import psycopg2.extras
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import PostgresAuthRepository
from session_repository import PostgresSessionRepository
from pct_api import router as pct_router
from pct_repository import PostgresPctRepository
from vct_api import router as vct_router
from vct_repository import PostgresVctRepository
from cmp_api import router as cmp_router
from cmp_repository import PostgresCmpRepository
from credential_service import hash_password


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")


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
    app.include_router(pct_router)
    app.include_router(vct_router)
    app.include_router(cmp_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)
    app.state.cmp_repository = PostgresCmpRepository(conn)
    client = TestClient(app, base_url="https://testserver")
    return app, client, conn


def _register_and_login(client, conn, email: str, role: str = "individual_buyer") -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO iam.users (business_code, primary_role, account_type, status) "
        "VALUES (%s, %s, 'individual', 'active') RETURNING id",
        (f"USR-{uuid.uuid4().hex[:12]}", role),
    )
    user_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO iam.user_identities (user_id, provider_type_id, external_identifier, credential_secret_hash, verified_at, is_primary) "
        "SELECT %s, ip.id, %s, %s, now(), true FROM iam.identity_providers ip WHERE ip.code = 'email_password'",
        (user_id, email, hash_password("Str0ngPass1!")),
    )
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200, resp.text


def _make_approved_part(client, conn) -> str:
    cur = conn.cursor()
    cur.execute("INSERT INTO pct.categories DEFAULT VALUES RETURNING id")
    category_id = cur.fetchone()["id"]
    part_id = client.post("/api/v1/pct/parts", json={"category_id": category_id}).json()["id"]
    client.post(f"/api/v1/pct/parts/{part_id}/approve")
    return part_id


def _make_valid_trim(client) -> str:
    m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
    client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
    model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
    gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]
    trim_resp = client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                             json={"fuel_type_ref_id": str(uuid.uuid4()), "transmission_type_ref_id": str(uuid.uuid4())})
    return trim_resp.json()["id"]


def _make_trim_model_year(client, trim_id: str, year: int) -> str:
    """
    مشتركة على مستوى الملف — كانت مُعرَّفة أصلًا فقط داخل
    TestBatch1YearSpecificCompatibilityOnLivePostgres (self._make_trim_model_year)،
    فسبَّبت AttributeError عند استدعائها من TestBatch1CompatibilityConcurrencyOnLivePostgres
    (Class مختلف تمامًا). نُقِلت هنا بنفس نمط _make_valid_trim/_make_approved_part
    أعلاه (دوال مشتركة على مستوى الملف)، لا تكرار للمنطق.
    """
    return client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": year}).json()["id"]


class TestCreateRecordOnLivePostgres:

    def test_admin_creates_record_for_approved_part_and_valid_trim(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    def test_regular_buyer_forbidden_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        # 1-3: تجهيز البيانات بصلاحية admin (اعتماد الشركة المصنّعة واعتماد
        # القطعة كلاهما يتطلب admin/super_admin؛ لا يمكن تجهيزهما بحساب buyer)
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)

        # 4: إنهاء جلسة admin صراحة قبل التبديل
        client.post("/api/v1/auth/logout")

        # 5: تسجيل الدخول بحساب عادي منفصل
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")

        # 6-7: محاولة إنشاء سجل CMP بحساب buyer، بالبيانات الجاهزة مسبقًا فقط
        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 403


class TestDuplicateConstraintOnLivePostgres:
    """يثبت uq_compatibility_general الفعلي (Batch 1: خلَف uq_compatibility_part_trim
    المُزال في 030 — التسمية تغيَّرت، السلوك المتوقَّع لهذا الاختبار (رفض تكرار
    زوج General نفسه) لم يتغيَّر)، لا محاكاة في الذاكرة."""

    def test_duplicate_pair_rejected_by_db(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)

        first = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert first.status_code == 201
        second = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert second.status_code == 409


class TestArchiveOnLivePostgres:

    def test_archive_then_get_shows_archived_status(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)
        record_id = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        archive_resp = client.post(f"/api/v1/cmp/records/{record_id}/archive")
        assert archive_resp.status_code == 200

        get_resp = client.get(f"/api/v1/cmp/records/{record_id}")
        assert get_resp.json()["status"] == "archived"


class TestBatch1YearSpecificCompatibilityOnLivePostgres:
    """Approved VCT Design Baseline §10-14: General/Year-specific على اتصال حي."""

    def test_year_specific_record_persisted_correctly(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)
        tmy_id = _make_trim_model_year(client, trim_id, 2019)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        assert resp.status_code == 201, resp.text
        assert resp.json()["trim_ref_id"] is None
        assert resp.json()["trim_model_year_ref_id"] == tmy_id

        cur = conn.cursor()
        cur.execute("SELECT trim_ref_id, trim_model_year_ref_id FROM cmp.compatibility_records WHERE id = %s",
                    (resp.json()["id"],))
        row = cur.fetchone()
        assert row["trim_ref_id"] is None
        assert row["trim_model_year_ref_id"] == tmy_id

    def test_general_and_year_specific_coexistence_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)
        tmy_id = _make_trim_model_year(client, trim_id, 2019)

        general_resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert general_resp.status_code == 201

        conflict_resp = client.post("/api/v1/cmp/records",
                                     json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        assert conflict_resp.status_code == 409
        assert conflict_resp.json()["detail"]["error_code"] == "COMPATIBILITY_LEVEL_CONFLICT"

    def test_old_check_constraint_removed_new_ones_present(self, app_and_client):
        """§14: يتحقق فعليًا من أسماء القيود الجديدة في information_schema، لا افتراضًا."""
        app, client, conn = app_and_client
        cur = conn.cursor()
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'cmp' AND tablename = 'compatibility_records'"
        )
        index_names = {r["indexname"] for r in cur.fetchall()}
        assert "uq_compatibility_general" in index_names
        assert "uq_compatibility_year_specific" in index_names
        assert "uq_compatibility_part_trim" not in index_names


class TestBatch1CompatibilityConcurrencyOnLivePostgres:
    """
    Approved VCT Design Baseline §13/§16: يثبت أن عمليتين متزامنتين (اتصالان
    منفصلان فعليًا، لا Thread وهمي على نفس الاتصال) لا تستطيعان إنشاء
    General وYear-specific متعارضين لنفس (قطعة، فئة) — القفل يفرض
    Serialization حقيقيًا، لا افتراضًا نظريًا. لا يكفي اختبار التسلسل العادي
    (مغطى أعلاه)؛ هذا يثبت السلوك تحت تزامن فعلي.
    """

    def test_concurrent_general_and_year_specific_inserts_never_both_succeed(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)
        tmy_id = _make_trim_model_year(client, trim_id, 2019)

        # نفس درس الجذر المُشخَّص في VCT concurrency test أعلاه (Repository
        # insert_* القديمة لا تُثبِّت Commit تلقائيًا): تثبيت بيانات الإعداد
        # صراحةً على conn قبل فتح أي اتصال متوازٍ، احتياطيًا (لا يغيّر سلوك
        # هذا الاختبار تحديدًا — insert_compatibility_record_with_lock لا FK ولا
        # Lookup على trim/trim_model_year الداخليين — لكنه يمنع أي مفاجأة مماثلة).
        conn.commit()

        # اتصالان منفصلان تمامًا (Repository لكل منهما) — يحاكي طلبَي API
        # متزامنين فعليًا من عمليتين مختلفتين، لا مجرد Thread بايثون مشترك.
        conn_a = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn_b = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        repo_a = PostgresCmpRepository(conn_a)
        repo_b = PostgresCmpRepository(conn_b)

        from cmp_service import CompatibilityLevelConflictError

        results = {}
        start_barrier = threading.Barrier(2)

        def insert_general():
            start_barrier.wait()
            try:
                repo_a.insert_compatibility_record_with_lock(
                    catalog_part_ref_id=part_id, resolved_trim_ref_id=trim_id,
                    trim_ref_id=trim_id, trim_model_year_ref_id=None,
                    fitment_type="unknown", compatibility_notes=None, source="catalog_admin",
                )
                results["general"] = ("success", None)
            except CompatibilityLevelConflictError as e:
                results["general"] = ("conflict", str(e))
            except Exception as e:  # noqa: BLE001 — التقاط شامل لتشخيص واضح، لا لإخفاء الفشل
                results["general"] = (f"unexpected:{type(e).__name__}", str(e))

        def insert_year_specific():
            start_barrier.wait()
            try:
                repo_b.insert_compatibility_record_with_lock(
                    catalog_part_ref_id=part_id, resolved_trim_ref_id=trim_id,
                    trim_ref_id=None, trim_model_year_ref_id=tmy_id,
                    fitment_type="unknown", compatibility_notes=None, source="catalog_admin",
                )
                results["year_specific"] = ("success", None)
            except CompatibilityLevelConflictError as e:
                results["year_specific"] = ("conflict", str(e))
            except Exception as e:  # noqa: BLE001
                results["year_specific"] = (f"unexpected:{type(e).__name__}", str(e))

        thread_a = threading.Thread(target=insert_general)
        thread_b = threading.Thread(target=insert_year_specific)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        assert not thread_a.is_alive(), "thread_a لم ينتهِ خلال المهلة — احتمال Deadlock حقيقي على القفل."
        assert not thread_b.is_alive(), "thread_b لم ينتهِ خلال المهلة — احتمال Deadlock حقيقي على القفل."
        assert "general" in results and "year_specific" in results, f"نتائج ناقصة: {results}"

        general_outcome, general_detail = results["general"]
        year_outcome, year_detail = results["year_specific"]
        assert general_outcome in ("success", "conflict"), f"general استثناء غير متوقَّع: {general_outcome} — {general_detail}"
        assert year_outcome in ("success", "conflict"), f"year_specific استثناء غير متوقَّع: {year_outcome} — {year_detail}"

        # الضمان الجوهري: بالضبط واحد نجح والآخر رُفِض — أبدًا الاثنان معًا
        # (بغضّ النظر عن أيهما فاز بالقفل، وهو غير محدَّد سلفًا بتصميم الاختبار).
        outcomes = sorted([general_outcome, year_outcome])
        assert outcomes == ["conflict", "success"], (
            f"general={general_outcome} ({general_detail}), year_specific={year_outcome} ({year_detail})"
        )

        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM cmp.compatibility_records WHERE catalog_part_ref_id = %s", (part_id,)
        )
        assert cur.fetchone()["c"] == 1, "يجب أن ينجح سجل واحد بالضبط، لا صفر ولا اثنان"

        conn_a.close()
        conn_b.close()
