"""
test_postgres_repositories.py — اختبارات حقيقية لطبقة PostgresXRepository نفسها
=====================================================================================
الحالة: Ready for PostgreSQL Execution — يستخدم أصناف PostgresXRepository
الفعلية من كل الخدمات الثلاث عشرة (لا SQL خام مباشر كما في
test_postgres_integration.py)؛ لم يُشغَّل بعد لغياب اتصال حي.

يفترض إضافة مسارات src لكل خدمة إلى sys.path (انظر conftest.py المقترَح
أدناه في التعليق) عند التشغيل الفعلي.
"""

import os
import sys
import uuid
from datetime import datetime
import pytest
import psycopg2
import psycopg2.extras

# عند التشغيل الفعلي: أضِف مسارات src لكل خدمة، مثال:
# sys.path.insert(0, "/path/to/services/auth/src")
# sys.path.insert(0, "/path/to/services/ntf/src")
# sys.path.insert(0, "/path/to/services/trm/src")
# ثم:
# from auth_repository import PostgresAuthRepository
# from ntf_repository import PostgresNtfRepository
# from ntf_service import Campaign, Delivery, Recipient
# from trm_repository import PostgresTrmRepository
# from trm_service import Rating

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")


@pytest.fixture
def conn():
    connection = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    yield connection
    connection.rollback()
    connection.close()


class TestPostgresNtfRepository:
    """يستخدم PostgresNtfRepository الفعلية (svc_ntf/src/ntf_repository.py) لا SQL مباشرًا."""

    def test_insert_and_fetch_campaign_via_real_repository(self, conn):
        from ntf_repository import PostgresNtfRepository
        from ntf_service import Campaign

        repo = PostgresNtfRepository(conn)
        campaign = Campaign(id="", created_by_user_ref_id=str(uuid.uuid4()),
                             title="حملة اختبار", body="محتوى", audience_type="static")
        saved = repo.insert_campaign(campaign)
        assert saved.id is not None

        fetched = repo.get_campaign_by_id(saved.id)
        assert fetched.title == "حملة اختبار"

    def test_dedup_via_real_repository_raises_on_duplicate_insert(self, conn):
        """يثبت أن PostgresNtfRepository.insert_recipient تُصعِّد UniqueViolation فعليًا من محرك DB."""
        from ntf_repository import PostgresNtfRepository
        from ntf_service import Campaign, Delivery, Recipient

        repo = PostgresNtfRepository(conn)
        campaign = repo.insert_campaign(Campaign(id="", created_by_user_ref_id=str(uuid.uuid4()),
                                                   title="t", body="b", audience_type="static"))
        delivery = repo.insert_delivery(Delivery(id="", campaign_id=campaign.id, campaign_version_snapshot=1,
                                                   correlation_id=str(uuid.uuid4())))
        user_ref = str(uuid.uuid4())
        repo.insert_recipient(Recipient(id="", delivery_id=delivery.id, user_ref_id=user_ref, channel_provider_code="in_app"))

        with pytest.raises(psycopg2.errors.UniqueViolation):
            repo.insert_recipient(Recipient(id="", delivery_id=delivery.id, user_ref_id=user_ref, channel_provider_code="email"))


class TestPostgresTrmRepository:
    """يستخدم PostgresTrmRepository الفعلية (svc_trm/src/trm_repository.py)."""

    def test_insert_rating_via_real_repository_translates_unique_violation(self, conn):
        """
        يثبت أن PostgresTrmRepository.insert_rating تترجم UniqueViolation إلى
        DuplicateRatingError (منطق أعمال)، لا استثناء psycopg2 خامًا يتسرَّب للمستدعي.
        يستوجب تطبيق migration 021_trm_unified_ratings.sql أولاً.
        """
        from trm_repository import PostgresTrmRepository
        from trm_service import Rating, DuplicateRatingError

        repo = PostgresTrmRepository(conn)
        rater = str(uuid.uuid4())
        pr_ref = str(uuid.uuid4())
        target_ref = str(uuid.uuid4())
        repo.insert_rating(Rating(id="", rated_by_user_ref_id=rater, target_type="seller",
                                   target_ref_id=target_ref, source_purchase_request_ref_id=pr_ref, score=5))

        with pytest.raises(DuplicateRatingError):
            repo.insert_rating(Rating(id="", rated_by_user_ref_id=rater, target_type="seller",
                                       target_ref_id=target_ref, source_purchase_request_ref_id=pr_ref, score=3))


class TestPostgresAuthRepository:
    """
    يستخدم PostgresAuthRepository الفعلية (svc_auth/src/auth_repository.py).
    لا يستوجب أي Fixture خارجي: مزوِّد الهوية (iam.identity_providers) مزروع
    فعليًا ضمن migration 015_cr005_phase1_identity_providers.sql نفسه (5 صفوف
    ثابتة، منها email_password المُستخدَم هنا)؛ المستخدم الوحيد المطلوب
    يُنشأ مباشرة عبر repo.create_user() المتوفرة أصلاً في المستودع.
    """

    def test_insert_identity_via_real_repository_translates_unique_violation(self, conn):
        from auth_repository import PostgresAuthRepository
        from auth_service import DuplicateIdentityError, UserIdentity

        repo = PostgresAuthRepository(conn)
        user_id = repo.create_user()
        external_id = f"user{uuid.uuid4()}@example.com"

        first = repo.insert_identity(UserIdentity(
            id="", user_id=user_id, provider_code="email_password",
            external_identifier=external_id, is_verified=True,
        ))
        assert first.id is not None

        with pytest.raises(DuplicateIdentityError):
            repo.insert_identity(UserIdentity(
                id="", user_id=user_id, provider_code="email_password",
                external_identifier=external_id, is_verified=True,
            ))


class TestPostgresSchedulerRepository:
    """يستخدم PostgresSchedulerRepository الفعلية (svc_scheduler/src/scheduler_repository.py)."""

    def test_insert_and_fetch_due_jobs_via_real_repository(self, conn):
        from scheduler_repository import PostgresSchedulerRepository
        from scheduler_service import ScheduledJob

        repo = PostgresSchedulerRepository(conn)
        job = repo.insert_job(ScheduledJob(id="", job_type="pur_expiration_check", target_ref_id=str(uuid.uuid4()),
                                            scheduled_at=datetime(2020, 1, 1)))  # تاريخ ماضٍ ليكون مستحقًا فورًا
        due = repo.get_pending_jobs_due_before(datetime.now())
        assert any(j.id == job.id for j in due)


class TestPostgresSearchRepository:
    """يستخدم PostgresSearchRepository الفعلية؛ للقراءة فقط (SRC لا تملك بيانات، تقرأ من STR/PCT/CMP)."""

    def test_fetch_matching_items_by_indexed_filters(self, conn):
        from search_repository import PostgresSearchRepository
        repo = PostgresSearchRepository(conn)
        results = repo.fetch_matching_items(trim_ref_id=str(uuid.uuid4()))
        assert isinstance(results, list)  # لا نتائج متوقَّعة لمعرّف عشوائي؛ يثبت فقط أن الاستعلام يُنفَّذ دون خطأ


class TestPostgresStoreRepository:
    """يستخدم PostgresStoreRepository الفعلية (svc_store/src/store_repository.py)."""

    def test_insert_and_fetch_store_via_real_repository(self, conn):
        from store_repository import PostgresStoreRepository
        from store_service import Store

        repo = PostgresStoreRepository(conn)
        store = repo.insert_store(Store(id="", owner_user_ref_id=str(uuid.uuid4()), status="active"))
        fetched = repo.get_store_by_id(store.id)
        assert fetched.owner_user_ref_id == store.owner_user_ref_id


class TestPostgresInventoryItemRepository:
    """يستخدم PostgresInventoryItemRepository الفعلية (svc_inventory/src/inventory_item_repository.py)."""

    def test_insert_and_fetch_item_via_real_repository(self, conn):
        from inventory_item_repository import PostgresInventoryItemRepository
        from inventory_item_service import InventoryItem

        # str.inventory_items.store_id يحمل REFERENCES str.stores(id) فعليًا
        # (خلافًا لـcatalog_part_ref_id/condition_ref_id، إشارتان مرجعيتان
        # وصفيتان فقط بلا FK حقيقي)؛ يلزم صف متجر حقيقي.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO str.stores (owner_user_ref_id) VALUES (%(owner)s) RETURNING id",
                {"owner": str(uuid.uuid4())},
            )
            store_id = cur.fetchone()["id"]

        repo = PostgresInventoryItemRepository(conn)
        item = repo.insert_item(InventoryItem(id="", store_id=store_id, catalog_part_ref_id=str(uuid.uuid4()),
                                              condition_ref_id=str(uuid.uuid4()), pricing_mode="contact_for_price"))
        fetched = repo.get_item_by_id(item.id)
        assert fetched.pricing_mode == "contact_for_price"


class TestPostgresPctRepository:
    """يستخدم PostgresPctRepository الفعلية (svc_pct/src/pct_repository.py)."""

    def test_insert_and_approve_part_via_real_repository(self, conn):
        from pct_repository import PostgresPctRepository
        from pct_service import CatalogPart

        # pct.categories لا تُدار عبر Repository (لا insert_category متاحة)؛
        # يلزم صف فئة حقيقي لتفادي انتهاك pct.catalog_parts.category_id FK.
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pct.categories DEFAULT VALUES RETURNING id")
            category_id = cur.fetchone()["id"]

        repo = PostgresPctRepository(conn)
        part = repo.insert_part(CatalogPart(id="", category_id=category_id, status="proposed"))
        assert repo.is_part_approved(part.id) is False
        part.status = "approved"
        repo.update_part(part)
        assert repo.is_part_approved(part.id) is True


class TestPostgresVctRepository:
    """يستخدم PostgresVctRepository الفعلية (svc_vct/src/vct_repository.py)."""

    def test_insert_manufacturer_and_check_trim_validity_via_real_repository(self, conn):
        from vct_repository import PostgresVctRepository
        from vct_service import Manufacturer

        repo = PostgresVctRepository(conn)
        manufacturer = repo.insert_manufacturer(Manufacturer(id="", status="approved"))
        assert manufacturer.id is not None
        # يجب أن يكون بصيغة UUID صالحة (العمود مُعرَّف كـUUID)؛ عدم الوجود هو
        # المطلوب اختباره هنا، لا صيغة نصية غير صالحة أصلًا (InvalidTextRepresentation
        # يختلف جوهريًا عن "غير موجود" ولا يعكس سيناريو عمل واقعيًا).
        assert repo.is_trim_valid(str(uuid.uuid4())) is False


class TestPostgresCmpRepository:
    """يستخدم PostgresCmpRepository الفعلية (svc_cmp/src/cmp_repository.py)؛ يختبر قيد التفرّد الحقيقي (قطعة+فئة)."""

    def test_duplicate_compatibility_pair_rejected_by_db(self, conn):
        from cmp_repository import PostgresCmpRepository
        from cmp_service import CompatibilityRecord

        repo = PostgresCmpRepository(conn)
        part_ref, trim_ref = str(uuid.uuid4()), str(uuid.uuid4())
        repo.insert_record(CompatibilityRecord(id="", catalog_part_ref_id=part_ref, trim_ref_id=trim_ref))
        with pytest.raises(psycopg2.errors.UniqueViolation):
            repo.insert_record(CompatibilityRecord(id="", catalog_part_ref_id=part_ref, trim_ref_id=trim_ref))


class TestPostgresOrderRepository:
    """يستخدم PostgresOrderRepository الفعلية (svc_order/src/order_repository.py)."""

    def test_insert_purchase_request_and_offer_via_real_repository(self, conn):
        from order_repository import PostgresOrderRepository
        from order_service import PurchaseRequest, Offer

        repo = PostgresOrderRepository(conn)
        pr = repo.insert_purchase_request(PurchaseRequest(
            id="", buyer_user_ref_id=str(uuid.uuid4()), catalog_part_ref_id=str(uuid.uuid4()),
            trim_ref_id=str(uuid.uuid4()), status="open"))
        offer = repo.insert_offer(Offer(id="", purchase_request_id=pr.id, seller_store_ref_id=str(uuid.uuid4()),
                                        amount=100.0, currency="SAR", provides_shipping=False))
        assert offer.purchase_request_id == pr.id


class TestPostgresMessageRepository:
    """يستخدم PostgresMessageRepository الفعلية (svc_message/src/message_repository.py)."""

    def test_insert_conversation_and_message_via_real_repository(self, conn):
        from message_repository import PostgresMessageRepository
        from message_service import Conversation, Message

        repo = PostgresMessageRepository(conn)
        conv = repo.insert_conversation(Conversation(id="", context_type="purchase_request", context_ref_id=str(uuid.uuid4())))
        msg = repo.insert_message(Message(id="", conversation_id=conv.id, sender_user_ref_id=str(uuid.uuid4()), body="test"))
        assert msg.conversation_id == conv.id


class TestPostgresMessageExtendedRepository:
    """يستخدم PostgresMessageExtendedRepository الفعلية (svc_message_ext/src/message_extended_repository.py)."""

    def test_upsert_presence_via_real_repository(self, conn):
        from message_extended_repository import PostgresMessageExtendedRepository
        from message_extended_service import UserPresence

        repo = PostgresMessageExtendedRepository(conn)
        user_ref = str(uuid.uuid4())
        repo.upsert_presence(UserPresence(user_ref_id=user_ref, is_online=True))
        fetched = repo.get_presence(user_ref)
        assert fetched.is_online is True


# ---------------------------------------------------------------------------
# جدول تغطية Repository (مرجعي، يُطبَّق فعليًا بعد التشغيل الحي)
# ---------------------------------------------------------------------------
REPOSITORY_COVERAGE = [
    {"repository": "PostgresNtfRepository", "test_class": "TestPostgresNtfRepository",
     "operations_covered": "insert_campaign, get_campaign_by_id, insert_delivery, insert_recipient (incl. UNIQUE violation)",
     "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresTrmRepository", "test_class": "TestPostgresTrmRepository",
     "operations_covered": "insert_rating (incl. UniqueViolation→DuplicateRatingError translation)",
     "execution_status": "Ready for PostgreSQL Execution (migration 021 now approved and applied to the sequence)"},
    {"repository": "PostgresAuthRepository", "test_class": "TestPostgresAuthRepository",
     "operations_covered": "insert_identity (planned; needs fixtures)",
     "execution_status": "Ready for PostgreSQL Execution (pending fixtures)"},
    {"repository": "PostgresSchedulerRepository", "test_class": "TestPostgresSchedulerRepository",
     "operations_covered": "insert_job, get_pending_jobs_due_before",
     "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresSearchRepository", "test_class": "TestPostgresSearchRepository",
     "operations_covered": "fetch_matching_items (read-only query path)", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresStoreRepository", "test_class": "TestPostgresStoreRepository",
     "operations_covered": "insert_store, get_store_by_id", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresInventoryItemRepository", "test_class": "TestPostgresInventoryItemRepository",
     "operations_covered": "insert_item, get_item_by_id", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresPctRepository", "test_class": "TestPostgresPctRepository",
     "operations_covered": "insert_part, update_part, is_part_approved", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresVctRepository", "test_class": "TestPostgresVctRepository",
     "operations_covered": "insert_manufacturer, is_trim_valid", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresCmpRepository", "test_class": "TestPostgresCmpRepository",
     "operations_covered": "insert_record (incl. UNIQUE violation on part+trim pair)", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresOrderRepository", "test_class": "TestPostgresOrderRepository",
     "operations_covered": "insert_purchase_request, insert_offer", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresMessageRepository", "test_class": "TestPostgresMessageRepository",
     "operations_covered": "insert_conversation, insert_message", "execution_status": "Ready for PostgreSQL Execution"},
    {"repository": "PostgresMessageExtendedRepository", "test_class": "TestPostgresMessageExtendedRepository",
     "operations_covered": "upsert_presence, get_presence", "execution_status": "Ready for PostgreSQL Execution"},
]

if __name__ == "__main__":
    import json
    print(json.dumps(REPOSITORY_COVERAGE, ensure_ascii=False, indent=2))
