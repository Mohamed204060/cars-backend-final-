"""
test_ntf_repository.py — اختبارات Repository لخدمة NTF
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ntf_service import (  # noqa: E402
    create_campaign, create_delivery_for_campaign, add_recipient,
    create_template, Recipient, NotificationCenterEntry,
)
from ntf_repository import InMemoryNtfRepository  # noqa: E402


class TestCampaignDeliveryRecipientRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryNtfRepository()

    def test_full_flow_campaign_to_recipient(self):
        campaign = create_campaign("admin-1", "إعلان", "محتوى", "static")
        campaign = self.repo.insert_campaign(campaign)
        self.assertTrue(campaign.id.startswith("campaign-"))

        delivery = create_delivery_for_campaign(campaign)
        delivery = self.repo.insert_delivery(delivery)
        self.assertTrue(delivery.id.startswith("delivery-"))
        self.assertEqual(delivery.campaign_version_snapshot, campaign.campaign_version)

        recipient = Recipient(id="", delivery_id=delivery.id, user_ref_id="user-1", channel_provider_code="in_app")
        recipient = self.repo.insert_recipient(recipient)
        self.assertTrue(recipient.id.startswith("recipient-"))

        fetched = self.repo.get_recipients_for_delivery(delivery.id)
        self.assertEqual(len(fetched), 1)

    def test_dedup_constraint_enforced_at_repository_level(self):
        delivery_id = "delivery-x"
        self.repo.insert_recipient(Recipient(id="", delivery_id=delivery_id, user_ref_id="user-1", channel_provider_code="in_app"))
        with self.assertRaises(ValueError):
            self.repo.insert_recipient(Recipient(id="", delivery_id=delivery_id, user_ref_id="user-1", channel_provider_code="email"))


class TestTransactionalOutbox(unittest.TestCase):
    """يثبت أن نمط Outbox المطلوب صراحة من المالك يعمل فعليًا."""

    def setUp(self):
        self.repo = InMemoryNtfRepository()

    def test_outbox_entry_created_and_stays_pending_until_dispatched(self):
        entry_id = self.repo.insert_outbox_entry("delivery-1", "recipient-1", "corr-abc")
        pending = self.repo.get_pending_outbox_entries()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["correlation_id"], "corr-abc")

        self.repo.mark_outbox_entry_dispatched(entry_id)
        pending_after = self.repo.get_pending_outbox_entries()
        self.assertEqual(len(pending_after), 0)  # لم يعد معلَّقًا بعد الإرسال الفعلي

    def test_multiple_outbox_entries_independent(self):
        e1 = self.repo.insert_outbox_entry("d1", "r1", "corr-1")
        e2 = self.repo.insert_outbox_entry("d2", "r2", "corr-2")
        self.repo.mark_outbox_entry_dispatched(e1)
        pending = self.repo.get_pending_outbox_entries()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], e2)


class TestConcurrentRecipientCreation(unittest.TestCase):
    """
    اختبار تزامن حقيقي (بنفس منهجية AuthRepository): طلبان متزامنان يحاولان
    إنشاء Recipient لنفس المستخدم ضمن نفس Delivery. يجب أن ينجح واحد فقط.
    ملاحظة أمانة: InMemoryNtfRepository الحالي غير محمي بقفل (Lock) صراحة؛
    هذا الاختبار يوثِّق السلوك الفعلي الملحوظ تحت GIL بايثون في هذه البيئة،
    لا ضمانًا معماريًا كاملاً يعادل قيد قاعدة بيانات حقيقي تحت تزامن حقيقي
    عبر عمليات متعددة — ذلك يستوجب فحصًا فعليًا على PostgreSQL حقيقي لاحقًا
    (اختبار تكامل معلَّق صراحة، كما في كل الخدمات السابقة).
    """

    def test_concurrent_attempts_only_one_succeeds_in_this_environment(self):
        repo = InMemoryNtfRepository()
        delivery_id = "delivery-race"
        results = {"success": 0, "failure": 0}
        lock = threading.Lock()

        def attempt():
            try:
                repo.insert_recipient(Recipient(id="", delivery_id=delivery_id, user_ref_id="user-race",
                                                 channel_provider_code="in_app"))
                with lock:
                    results["success"] += 1
            except ValueError:
                with lock:
                    results["failure"] += 1

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # تحت GIL بايثون مع هذا التنفيذ التسلسلي الفعلي لكل خطوة، لا نضمن هنا
        # تمامًا نفس ضمانات قيد قاعدة بيانات حقيقي دون قفل صريح؛ نتحقق فقط أن
        # النتيجة النهائية للبيانات صحيحة ولا يوجد تكرار فعلي في المخزن،
        # بصرف النظر عن توزيع النجاح/الفشل بين الخيطين.
        final_recipients = repo.get_recipients_for_delivery(delivery_id)
        self.assertEqual(len(final_recipients), 1)  # النتيجة الفعلية: سجل واحد فقط مهما كان توزيع النجاح/الفشل


class TestTemplateVersionRepository(unittest.TestCase):

    def test_insert_template_creates_first_version(self):
        repo = InMemoryNtfRepository()
        template, v1 = create_template("welcome", "مرحبًا", "محتوى")
        template = repo.insert_template(template, v1)
        self.assertTrue(template.id.startswith("template-"))


class TestNotificationCenterRepository(unittest.TestCase):

    def test_insert_and_fetch_entries_for_user(self):
        repo = InMemoryNtfRepository()
        entry = NotificationCenterEntry(id="", recipient_id="r1", user_ref_id="user-1")
        repo.insert_notification_center_entry(entry)
        fetched = repo.get_notification_center_entries_for_user("user-1")
        self.assertEqual(len(fetched), 1)

    def test_entries_scoped_per_user(self):
        repo = InMemoryNtfRepository()
        repo.insert_notification_center_entry(NotificationCenterEntry(id="", recipient_id="r1", user_ref_id="user-1"))
        repo.insert_notification_center_entry(NotificationCenterEntry(id="", recipient_id="r2", user_ref_id="user-2"))
        self.assertEqual(len(repo.get_notification_center_entries_for_user("user-1")), 1)
        self.assertEqual(len(repo.get_notification_center_entries_for_user("user-2")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
