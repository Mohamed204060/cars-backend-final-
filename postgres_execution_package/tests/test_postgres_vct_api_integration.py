"""
test_postgres_vct_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة VCT على PostgreSQL حي (VCT Contract Extension)
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
from vct_api import router as vct_router
from vct_repository import PostgresVctRepository
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
    app.include_router(vct_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)
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


class TestManufacturerLifecycleOnLivePostgres:

    def test_propose_and_approve_manufacturer(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_regular_buyer_cannot_approve_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert resp.status_code == 403


class TestFullHierarchyOnLivePostgres:

    def test_full_manufacturer_to_trim_chain(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"chain{uuid.uuid4().hex[:6]}@example.com", role="super_admin")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
        gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]

        trim_resp = client.post(
            f"/api/v1/vct/generations/{gen_id}/trims",
            json={"fuel_type_ref_id": str(uuid.uuid4()), "transmission_type_ref_id": str(uuid.uuid4())},
        )
        assert trim_resp.status_code == 201

        get_resp = client.get(f"/api/v1/vct/trims/{trim_resp.json()['id']}")
        assert get_resp.status_code == 200

    def test_model_under_unapproved_manufacturer_returns_409_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"unapproved{uuid.uuid4().hex[:6]}@example.com")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]  # لم يُعتمَد
        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/models")
        assert resp.status_code == 409


def _make_full_chain(client):
    m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
    client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
    model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
    gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]
    trim_id = client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                           json={"fuel_type_ref_id": str(uuid.uuid4()), "transmission_type_ref_id": str(uuid.uuid4())}
                           ).json()["id"]
    return gen_id, trim_id


class TestBatch1TrimModelYearsOnLivePostgres:

    def test_uq_trim_model_years_trim_year_enforced_by_db(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        _, trim_id = _make_full_chain(client)

        first = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2020})
        assert first.status_code == 201
        second = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2020})
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "DUPLICATE_TRIM_MODEL_YEAR"

    def test_generation_range_validated_against_real_join_to_trims(self, app_and_client):
        """§4: يثبت أن get_generation_year_range_for_trim يحل الجيل الفعلي عبر JOIN حقيقي، لا محاكاة."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        gen_id, trim_id = _make_full_chain(client)
        client.post(f"/api/v1/vct/generations/{gen_id}/year-range", json={"start_year": 2015, "end_year": 2020})

        rejected = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2021})
        assert rejected.status_code == 409
        accepted = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2018})
        assert accepted.status_code == 201


class TestBatch1MarketAvailabilityOnLivePostgres:
    """Approved VCT Design Baseline §6-9."""

    def test_no_rows_is_global_availability_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        _, trim_id = _make_full_chain(client)

        from vct_service import is_trim_available_in_country_via_repository
        assert is_trim_available_in_country_via_repository(
            app.state.vct_repository, country_ref_id=str(uuid.uuid4()), trim_ref_id=trim_id) is True

    def test_whitelist_enforced_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        _, trim_id = _make_full_chain(client)
        sa = str(uuid.uuid4())
        ae = str(uuid.uuid4())
        resp = client.post(f"/api/v1/vct/trims/{trim_id}/market-availability", json={"country_ref_id": sa})
        assert resp.status_code == 201

        from vct_service import is_trim_available_in_country_via_repository
        assert is_trim_available_in_country_via_repository(
            app.state.vct_repository, country_ref_id=sa, trim_ref_id=trim_id) is True
        assert is_trim_available_in_country_via_repository(
            app.state.vct_repository, country_ref_id=ae, trim_ref_id=trim_id) is False

    def test_partial_unique_indexes_enforced_by_db(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        _, trim_id = _make_full_chain(client)
        sa = str(uuid.uuid4())

        first = client.post(f"/api/v1/vct/trims/{trim_id}/market-availability", json={"country_ref_id": sa})
        assert first.status_code == 201
        # نفس (trim, country) مرة ثانية — يجب أن يُصطدَم بـuq_market_availability_trim_country
        # عبر Advisory Lock + إعادة الفحص (لا نتوقع IntegrityError خامًا يتسرَّب هنا لأن
        # الفحص المسبَق داخل نفس القفل يمنعه أصلًا عبر MarketAvailabilityLevelConflictError
        # فقط لتعارض المستوى العام/السنوي — التكرار الدقيق لنفس (trim, country) هنا لم
        # يُغطَّ بفحص صريح مكافئ في هذه الدفعة؛ الحارس النهائي هو قيد DB نفسه.
        with pytest.raises(Exception):
            client.post(f"/api/v1/vct/trims/{trim_id}/market-availability", json={"country_ref_id": sa})


class TestBatch1MarketAvailabilityConcurrencyOnLivePostgres:
    """
    §17: يثبت أن عمليتين متزامنتين (اتصالان منفصلان فعليًا) لا تستطيعان خلط
    Trim-level وYear-specific Availability لنفس Trim.
    """

    def test_concurrent_trim_level_and_year_specific_never_both_succeed(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        _, trim_id = _make_full_chain(client)
        tmy_id = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).json()["id"]

        # Root-Cause (مؤكَّد): PostgresVctRepository.insert_trim_model_year (نمط
        # قديم قائم مسبقًا في كل دوال insert_* الأصلية لـVCT، لا خلل مُستحدَث في
        # هذه الدفعة) يستخدم `with self._connection.cursor()` فقط — بلا
        # `with self._connection:` — فلا يُثبَّت (Commit) الصف تلقائيًا. الصف
        # يبقى داخل معاملة `conn` المفتوحة فقط، وبالتالي **غير مرئي** لأي اتصال
        # PostgreSQL منفصل فعليًا (conn_a/conn_b أدناه) قبل تثبيته صراحةً — هذا
        # سلوك PostgreSQL القياسي (READ COMMITTED)، لا افتراضًا. إثبات صريح قبل
        # المتابعة، عبر اتصال مستقل ثالث تمامًا:
        conn_verify = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        cur_verify = conn_verify.cursor()
        cur_verify.execute("SELECT id, trim_ref_id, year FROM vct.trim_model_years WHERE id = %s", (tmy_id,))
        assert cur_verify.fetchone() is None, (
            "توقُّع مسبق: الصف يجب ألا يكون مرئيًا بعد من اتصال منفصل قبل commit صريح على conn — "
            "إن ظهر هذا الفشل فالسبب الجذري المُشخَّص هنا لم يعد صحيحًا ويستوجب فحصًا جديدًا."
        )
        conn_verify.close()

        # الإصلاح الأصغر والصحيح (Test/Fixture-level فقط، بلا لمس Repository/Business
        # Logic): تثبيت بيانات الإعداد صراحةً على نفس اتصال conn قبل فتح أي اتصال
        # متوازٍ — يطابق تمامًا سلوك عميل PostgreSQL حقيقي (Commit بعد كل طلب).
        conn.commit()

        # إعادة نفس فحص الرؤية أعلاه بعد commit — يجب أن يظهر الصف الآن من اتصال مستقل
        conn_verify2 = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        cur_verify2 = conn_verify2.cursor()
        cur_verify2.execute("SELECT id, trim_ref_id, year FROM vct.trim_model_years WHERE id = %s", (tmy_id,))
        row = cur_verify2.fetchone()
        assert row is not None, "بعد commit صريح، يجب أن يكون صف trim_model_years مرئيًا لاتصال منفصل — فشل هذا يعني سببًا جذريًا مختلفًا."
        assert row["trim_ref_id"] == trim_id and row["year"] == 2019
        conn_verify2.close()

        conn_a = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn_b = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        repo_a = PostgresVctRepository(conn_a)
        repo_b = PostgresVctRepository(conn_b)

        from vct_service import MarketAvailabilityLevelConflictError

        # كل Thread يخزّن ("outcome", detail) دائمًا — لا يترك مفتاحًا غائبًا (None
        # ضمنيًا) أبدًا، بغضّ النظر عن نوع الاستثناء، لمنع فشل ثانوي مضلِّل من
        # sorted() عند حدوث استثناء غير متوقَّع (البند 7).
        results = {}
        start_barrier = threading.Barrier(2)

        def insert_trim_level():
            start_barrier.wait()
            try:
                repo_a.insert_market_availability_with_lock(country_ref_id=str(uuid.uuid4()), trim_ref_id=trim_id)
                results["trim_level"] = ("success", None)
            except MarketAvailabilityLevelConflictError as e:
                results["trim_level"] = ("conflict", str(e))
            except Exception as e:  # noqa: BLE001 — نلتقط كل شيء عمدًا لتشخيص واضح، لا لإخفاء الفشل
                results["trim_level"] = (f"unexpected:{type(e).__name__}", str(e))

        def insert_year_level():
            start_barrier.wait()
            try:
                repo_b.insert_market_availability_with_lock(
                    country_ref_id=str(uuid.uuid4()), trim_model_year_ref_id=tmy_id,
                )
                results["year_level"] = ("success", None)
            except MarketAvailabilityLevelConflictError as e:
                results["year_level"] = ("conflict", str(e))
            except Exception as e:  # noqa: BLE001
                results["year_level"] = (f"unexpected:{type(e).__name__}", str(e))

        thread_a = threading.Thread(target=insert_trim_level)
        thread_b = threading.Thread(target=insert_year_level)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        assert not thread_a.is_alive(), "thread_a لم ينتهِ خلال المهلة (15 ثانية) — احتمال Deadlock حقيقي على القفل."
        assert not thread_b.is_alive(), "thread_b لم ينتهِ خلال المهلة (15 ثانية) — احتمال Deadlock حقيقي على القفل."

        assert "trim_level" in results, "thread_a انتهى بلا تسجيل أي نتيجة إطلاقًا — خلل غير متوقَّع في آلية الالتقاط نفسها."
        assert "year_level" in results, "thread_b انتهى بلا تسجيل أي نتيجة إطلاقًا — خلل غير متوقَّع في آلية الالتقاط نفسها."

        trim_outcome, trim_detail = results["trim_level"]
        year_outcome, year_detail = results["year_level"]

        # فشل واضح فورًا عند أي استثناء غير متوقَّع (بدل TypeError ثانوي مضلِّل من sorted)
        assert trim_outcome in ("success", "conflict"), f"trim_level استثناء غير متوقَّع: {trim_outcome} — {trim_detail}"
        assert year_outcome in ("success", "conflict"), f"year_level استثناء غير متوقَّع: {year_outcome} — {year_detail}"

        outcomes = sorted([trim_outcome, year_outcome])
        assert outcomes == ["conflict", "success"], (
            f"يجب أن ينجح واحد بالضبط ويُرفَض الآخر بـMarketAvailabilityLevelConflictError، لا الاثنان معًا ولا كلاهما فشل: "
            f"trim_level={trim_outcome} ({trim_detail}), year_level={year_outcome} ({year_detail})"
        )

        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM vct.trim_market_availability WHERE trim_ref_id = %s "
            "OR trim_model_year_ref_id = %s",
            (trim_id, tmy_id),
        )
        assert cur.fetchone()["c"] == 1, "يجب أن ينجح صف واحد بالضبط، لا صفر ولا اثنان"

        conn_a.close()
        conn_b.close()
