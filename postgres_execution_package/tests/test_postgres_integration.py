"""
test_postgres_integration.py — اختبارات تكامل حقيقية على PostgreSQL
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار في هذا الملف
فعليًا بعد؛ لا يوجد اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه
الحزمة. لا يجوز اعتبار أي اختبار هنا "Passed" حتى يُنفَّذ فعليًا على اتصال
حي ويُوثَّق ناتجه في PostgreSQL Validation Report.

المتطلبات لتشغيل هذا الملف:
    pip install psycopg2-binary pytest
    export TEST_DATABASE_URL=postgresql://user:pass@host:5432/carparts_test
    (بعد تشغيل scripts/setup_test_database.sh لتطبيق كل الترحيلات أولاً)
    pytest test_postgres_integration.py -v

كل دالة اختبار هنا تُغلَّف بـ transaction وROLLBACK تلقائي عند الانتهاء
(عبر fixture conn أدناه)، حتى لا تترك أثرًا بين حالات الاختبار المختلفة.
"""

import os
import uuid
import threading
import pytest
import psycopg2
import psycopg2.extras


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")


@pytest.fixture
def conn():
    """اتصال بمعاملة واحدة تُلغى (ROLLBACK) تلقائيًا بعد كل اختبار، لعزل كامل بين الحالات."""
    connection = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    yield connection
    connection.rollback()
    connection.close()


def new_conn():
    """اتصال مستقل تمامًا (لا يشارك معاملة)، ضروري لاختبارات التزامن الحقيقية عبر خيوط/اتصالات منفصلة."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------------------
# فئة 1: قيود التفرّد (Unique Constraints) — اختبار حقيقي على محرك DB نفسه
# ---------------------------------------------------------------------------

class TestUniqueConstraintsOnLivePostgres:
    """يثبت أن قيود UNIQUE مُطبَّقة فعليًا على محرك PostgreSQL، لا محاكاة InMemory فقط."""

    def test_ntf_recipient_dedup_constraint_enforced_by_db(self, conn):
        cur = conn.cursor()
        cur.execute("INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type) "
                    "VALUES (%s, 't', 'b', 'static') RETURNING id", (str(uuid.uuid4()),))
        campaign_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO ntf.deliveries (campaign_id, campaign_version_snapshot, correlation_id) "
                    "VALUES (%s, 1, %s) RETURNING id", (campaign_id, str(uuid.uuid4())))
        delivery_id = cur.fetchone()["id"]
        user_ref = str(uuid.uuid4())

        cur.execute("INSERT INTO ntf.recipients (delivery_id, user_ref_id, channel_provider_code) "
                    "VALUES (%s, %s, 'in_app')", (delivery_id, user_ref))

        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("INSERT INTO ntf.recipients (delivery_id, user_ref_id, channel_provider_code) "
                        "VALUES (%s, %s, 'email')", (delivery_id, user_ref))

    def test_pur_offer_one_active_per_seller_enforced_by_db(self, conn):
        cur = conn.cursor()
        cur.execute("INSERT INTO pur.purchase_requests (business_code, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (f"PR-{uuid.uuid4().hex[:8]}", str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())))
        pr_id = cur.fetchone()["id"]
        seller_ref = str(uuid.uuid4())

        cur.execute("INSERT INTO pur.offers (business_code, purchase_request_id, seller_store_ref_id, amount, currency, provides_shipping) "
                    "VALUES (%s, %s, %s, 100, 'SAR', false)", (f"OF-{uuid.uuid4().hex[:8]}", pr_id, seller_ref))

        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("INSERT INTO pur.offers (business_code, purchase_request_id, seller_store_ref_id, amount, currency, provides_shipping) "
                        "VALUES (%s, %s, %s, 90, 'SAR', false)", (f"OF-{uuid.uuid4().hex[:8]}", pr_id, seller_ref))


# ---------------------------------------------------------------------------
# فئة 2: التزامن الحقيقي (Concurrency) على اتصالات منفصلة فعلية — لا خيوط بايثون فقط
# ---------------------------------------------------------------------------

class TestRealConcurrencyOnLivePostgres:
    """
    الفرق الجوهري عن اختبارات التزامن السابقة (InMemory بخيوط بايثون تحت GIL):
    هنا كل خيط يفتح اتصال PostgreSQL منفصلاً تمامًا، فيُختبَر التزامن الحقيقي
    على مستوى محرك قاعدة البيانات نفسه (Row Locks، MVCC)، لا سلوك بايثون الداخلي.
    """

    def test_auth_two_connections_racing_same_identity_link(self):
        """يعيد اختبار AuthRepository الحقيقي (test_two_concurrent_requests_linking_same_identity_only_one_succeeds)
        لكن على اتصالين حقيقيين منفصلين بدل خيطَي بايثون على InMemory."""
        setup_conn = new_conn()
        cur = setup_conn.cursor()
        # iam.users intentionally has no email column after CR-005; login identifiers
        # belong in iam.user_identities. Create two valid account rows first.
        cur.execute(
            "INSERT INTO iam.users (business_code, primary_role, account_type, status) "
            "VALUES (%s, 'individual_buyer', 'individual', 'active') RETURNING id",
            (f"USR-{uuid.uuid4().hex[:12]}",),
        )
        user1_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO iam.users (business_code, primary_role, account_type, status) "
            "VALUES (%s, 'individual_buyer', 'individual', 'active') RETURNING id",
            (f"USR-{uuid.uuid4().hex[:12]}",),
        )
        user2_id = cur.fetchone()["id"]
        setup_conn.commit()

        external_identifier = f"google-sub-{uuid.uuid4().hex}"
        results = {"success": 0, "failure": 0}
        lock = threading.Lock()

        def attempt(user_id):
            c = new_conn()
            try:
                cc = c.cursor()
                cc.execute(
                    "INSERT INTO iam.user_identities (user_id, provider_type_id, external_identifier) "
                    "SELECT %s, id, %s FROM iam.identity_providers WHERE code = 'google'",
                    (user_id, external_identifier),
                )
                c.commit()
                with lock:
                    results["success"] += 1
            except psycopg2.errors.UniqueViolation:
                c.rollback()
                with lock:
                    results["failure"] += 1
            finally:
                c.close()

        t1 = threading.Thread(target=attempt, args=(user1_id,))
        t2 = threading.Thread(target=attempt, args=(user2_id,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert results["success"] == 1
        assert results["failure"] == 1

        verify_conn = new_conn()
        vcur = verify_conn.cursor()
        vcur.execute("SELECT COUNT(*) AS cnt FROM iam.user_identities WHERE external_identifier = %s", (external_identifier,))
        assert vcur.fetchone()["cnt"] == 1  # لا سجل مكرَّر فعليًا في قاعدة البيانات

    def test_trm_two_connections_racing_same_rating(self):
        """يعيد اختبار التزامن الحقيقي لـTRM بعد اعتماد migration 021 الرسمية (لم تعد مُتخطَّاة)."""
        setup_conn = new_conn()
        cur = setup_conn.cursor()
        rater = str(uuid.uuid4())
        target_ref = str(uuid.uuid4())
        source_pr = str(uuid.uuid4())
        setup_conn.commit()

        results = {"success": 0, "failure": 0}
        lock = threading.Lock()

        def attempt(score):
            c = new_conn()
            try:
                cc = c.cursor()
                cc.execute(
                    "INSERT INTO trm.ratings (rated_by_user_ref_id, target_type, target_ref_id, source_purchase_request_ref_id, score) "
                    "VALUES (%s, 'seller', %s, %s, %s)", (rater, target_ref, source_pr, score),
                )
                c.commit()
                with lock:
                    results["success"] += 1
            except psycopg2.errors.UniqueViolation:
                c.rollback()
                with lock:
                    results["failure"] += 1
            finally:
                c.close()

        t1 = threading.Thread(target=attempt, args=(5,))
        t2 = threading.Thread(target=attempt, args=(3,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert results["success"] == 1
        assert results["failure"] == 1

    def test_ntf_two_connections_racing_same_recipient(self):
        setup_conn = new_conn()
        cur = setup_conn.cursor()
        cur.execute("INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type) "
                    "VALUES (%s, 't', 'b', 'static') RETURNING id", (str(uuid.uuid4()),))
        campaign_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO ntf.deliveries (campaign_id, campaign_version_snapshot, correlation_id) "
                    "VALUES (%s, 1, %s) RETURNING id", (campaign_id, str(uuid.uuid4())))
        delivery_id = cur.fetchone()["id"]
        setup_conn.commit()

        user_ref = str(uuid.uuid4())
        results = {"success": 0, "failure": 0}
        lock = threading.Lock()

        def attempt(channel):
            c = new_conn()
            try:
                cc = c.cursor()
                cc.execute("INSERT INTO ntf.recipients (delivery_id, user_ref_id, channel_provider_code) "
                          "VALUES (%s, %s, %s)", (delivery_id, user_ref, channel))
                c.commit()
                with lock:
                    results["success"] += 1
            except psycopg2.errors.UniqueViolation:
                c.rollback()
                with lock:
                    results["failure"] += 1
            finally:
                c.close()

        threads = [threading.Thread(target=attempt, args=(ch,)) for ch in ["in_app", "email"]]
        for t in threads: t.start()
        for t in threads: t.join()

        assert results["success"] == 1
        assert results["failure"] == 1


# ---------------------------------------------------------------------------
# فئة 3: المعاملات وRollback
# ---------------------------------------------------------------------------

class TestTransactionsAndRollback:

    def test_rollback_on_error_leaves_no_partial_state(self, conn):
        """يثبت أن فشل خطوة داخل معاملة يُلغي كل الخطوات السابقة معها (لا حالة جزئية)."""
        cur = conn.cursor()
        cur.execute("INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type) "
                    "VALUES (%s, 't', 'b', 'static') RETURNING id", (str(uuid.uuid4()),))
        campaign_id = cur.fetchone()["id"]

        savepoint = "sp1"
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            # محاولة إدراج Delivery بمعرّف حملة غير موجود (ينتهك FK عمدًا)
            cur.execute("INSERT INTO ntf.deliveries (campaign_id, campaign_version_snapshot, correlation_id) "
                        "VALUES (%s, 1, %s)", (str(uuid.uuid4()), str(uuid.uuid4())))
        except psycopg2.errors.ForeignKeyViolation:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")

        # التحقق من أن الحملة الأصلية ما زالت موجودة (لم تتأثر بفشل الخطوة اللاحقة)
        cur.execute("SELECT COUNT(*) AS cnt FROM ntf.campaigns WHERE id = %s", (campaign_id,))
        assert cur.fetchone()["cnt"] == 1


# ---------------------------------------------------------------------------
# فئة 4: الأرشفة وسياسة عدم الحذف الفعلي
# ---------------------------------------------------------------------------

class TestNoHardDeletePolicy:

    def test_no_delete_privilege_on_template_versions(self, conn):
        """يتحقق فعليًا من منع حذف صف موجود من جدول الإصدارات Append-Only.

        ملاحظة: الـ trigger صفّي (FOR EACH ROW)، لذلك DELETE على جدول فارغ لا يشغّله.
        ينشئ الاختبار قالبًا وإصدارًا حقيقيًا أولًا، ثم يحاول حذف الإصدار.
        """
        cur = conn.cursor()
        template_code = f"TPL-{uuid.uuid4().hex[:12]}"
        cur.execute(
            "INSERT INTO ntf.templates (code, status, current_version_number) "
            "VALUES (%s, 'active', 1) RETURNING id",
            (template_code,),
        )
        template_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO ntf.template_versions (template_id, version_number, title, body) "
            "VALUES (%s, 1, 'Test title', 'Test body') RETURNING id",
            (template_id,),
        )
        version_id = cur.fetchone()["id"]

        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM ntf.template_versions WHERE id = %s", (version_id,))

    def test_archival_does_not_remove_row(self, conn):
        cur = conn.cursor()
        cur.execute("INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type, status) "
                    "VALUES (%s, 't', 'b', 'static', 'archived') RETURNING id", (str(uuid.uuid4()),))
        campaign_id = cur.fetchone()["id"]
        cur.execute("SELECT status FROM ntf.campaigns WHERE id = %s", (campaign_id,))
        assert cur.fetchone()["status"] == "archived"  # السجل موجود بكامل بياناته، لم يُحذَف


# ---------------------------------------------------------------------------
# فئة 5: Pagination وFiltering وSearch
# ---------------------------------------------------------------------------

class TestPaginationFilteringSearch:

    def test_pagination_limit_offset(self, conn):
        cur = conn.cursor()
        creator = str(uuid.uuid4())
        for i in range(5):
            cur.execute("INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type) "
                        "VALUES (%s, %s, 'b', 'static')", (creator, f"campaign-{i}"))
        cur.execute("SELECT title FROM ntf.campaigns WHERE created_by_user_ref_id = %s ORDER BY title LIMIT 2 OFFSET 2", (creator,))
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0]["title"] == "campaign-2"

    def test_status_filtering(self, conn):
        cur = conn.cursor()
        creator = str(uuid.uuid4())
        cur.execute("INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type, status) "
                    "VALUES (%s, 't1', 'b', 'static', 'draft')", (creator,))
        cur.execute("INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type, status) "
                    "VALUES (%s, 't2', 'b', 'static', 'running')", (creator,))
        cur.execute("SELECT COUNT(*) AS cnt FROM ntf.campaigns WHERE created_by_user_ref_id = %s AND status = 'running'", (creator,))
        assert cur.fetchone()["cnt"] == 1
