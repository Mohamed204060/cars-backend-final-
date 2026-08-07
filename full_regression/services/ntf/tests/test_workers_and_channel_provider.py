"""
test_workers_and_channel_provider.py — اختبارات عمال المعالجة ومزوِّد القناة
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ntf_service import Recipient, ChannelProviderInfo  # noqa: E402
from ntf_repository import InMemoryNtfRepository  # noqa: E402
from channel_provider import InAppChannelProvider, ChannelProviderRegistry, DeliveryResult  # noqa: E402
from workers import OutboxWorker, DeliveryWorker, RetryWorker, ChannelHealthCheckWorker  # noqa: E402


class TestInAppChannelProvider(unittest.TestCase):

    def test_send_creates_notification_center_entry(self):
        repo = InMemoryNtfRepository()
        provider = InAppChannelProvider(repo)
        result = provider.send("user-1", "عنوان", "نص", "corr-1")
        self.assertTrue(result.success)
        entries = repo.get_notification_center_entries_for_user("user-1")
        self.assertEqual(len(entries), 1)

    def test_provider_code_is_in_app(self):
        provider = InAppChannelProvider(InMemoryNtfRepository())
        self.assertEqual(provider.code, "in_app")


class TestChannelProviderRegistry(unittest.TestCase):

    def test_register_and_get(self):
        registry = ChannelProviderRegistry()
        provider = InAppChannelProvider(InMemoryNtfRepository())
        registry.register(provider)
        self.assertIs(registry.get("in_app"), provider)

    def test_unknown_provider_returns_none(self):
        registry = ChannelProviderRegistry()
        self.assertIsNone(registry.get("whatsapp"))


class TestOutboxWorker(unittest.TestCase):

    def test_run_one_cycle_dispatches_all_pending(self):
        repo = InMemoryNtfRepository()
        repo.insert_outbox_entry("d1", "r1", "corr-1")
        repo.insert_outbox_entry("d2", "r2", "corr-2")

        enqueued = []
        worker = OutboxWorker(repo, real_queue_enqueue_fn=lambda entry: enqueued.append(entry))

        count = worker.run_one_cycle()
        self.assertEqual(count, 2)
        self.assertEqual(len(enqueued), 2)
        self.assertEqual(len(repo.get_pending_outbox_entries()), 0)

    def test_second_cycle_dispatches_nothing_new(self):
        repo = InMemoryNtfRepository()
        repo.insert_outbox_entry("d1", "r1", "corr-1")
        worker = OutboxWorker(repo, real_queue_enqueue_fn=lambda entry: None)
        worker.run_one_cycle()
        second_count = worker.run_one_cycle()
        self.assertEqual(second_count, 0)


class TestDeliveryWorker(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryNtfRepository()
        self.registry = ChannelProviderRegistry()
        self.registry.register(InAppChannelProvider(self.repo))
        self.worker = DeliveryWorker(self.repo, self.registry)

    def test_successful_delivery_via_in_app(self):
        recipient = self.repo.insert_recipient(Recipient(id="", delivery_id="d1", user_ref_id="user-1",
                                                           channel_provider_code="in_app"))
        result = self.worker.process_recipient(recipient, "عنوان", "نص", "corr-1")
        self.assertEqual(result.status, "delivered")

    def test_unknown_provider_marks_failed(self):
        recipient = self.repo.insert_recipient(Recipient(id="", delivery_id="d1", user_ref_id="user-1",
                                                           channel_provider_code="unknown_channel"))
        result = self.worker.process_recipient(recipient, "عنوان", "نص", "corr-1")
        self.assertEqual(result.status, "failed")

    def test_idempotency_skips_already_delivered_recipient(self):
        recipient = self.repo.insert_recipient(Recipient(id="", delivery_id="d1", user_ref_id="user-1",
                                                           channel_provider_code="in_app"))
        self.worker.process_recipient(recipient, "عنوان", "نص", "corr-1")
        entries_before = len(self.repo.get_notification_center_entries_for_user("user-1"))

        # إعادة معالجة نفس المستلِم (محاكاة إعادة تنفيذ Job بسبب إعادة تشغيل Worker)
        result = self.worker.process_recipient(recipient, "عنوان", "نص", "corr-1")

        entries_after = len(self.repo.get_notification_center_entries_for_user("user-1"))
        self.assertEqual(entries_before, entries_after)  # لا إرسال مكرَّر فعليًا (Idempotency)
        self.assertEqual(result.status, "delivered")


class TestRetryWorker(unittest.TestCase):

    def test_retry_eligible_failed_recipient(self):
        repo = InMemoryNtfRepository()
        registry = ChannelProviderRegistry()
        registry.register(InAppChannelProvider(repo))
        delivery_worker = DeliveryWorker(repo, registry)
        retry_worker = RetryWorker(repo, delivery_worker, max_retries=3)

        recipient = repo.insert_recipient(Recipient(id="", delivery_id="d1", user_ref_id="user-1",
                                                      channel_provider_code="in_app", status="failed"))
        result = retry_worker.retry_if_eligible(recipient, "عنوان", "نص", "corr-1")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "delivered")

    def test_retry_exhausted_returns_none(self):
        repo = InMemoryNtfRepository()
        registry = ChannelProviderRegistry()
        registry.register(InAppChannelProvider(repo))
        delivery_worker = DeliveryWorker(repo, registry)
        retry_worker = RetryWorker(repo, delivery_worker, max_retries=3)

        recipient = Recipient(id="r1", delivery_id="d1", user_ref_id="user-1",
                               channel_provider_code="in_app", status="failed", retry_count=3)
        result = retry_worker.retry_if_eligible(recipient, "عنوان", "نص", "corr-1")
        self.assertIsNone(result)  # استُنفِدت المحاولات؛ يستوجب Dead Letter Queue لاحقًا

    def test_non_failed_recipient_not_retried(self):
        repo = InMemoryNtfRepository()
        registry = ChannelProviderRegistry()
        delivery_worker = DeliveryWorker(repo, registry)
        retry_worker = RetryWorker(repo, delivery_worker)

        recipient = Recipient(id="r1", delivery_id="d1", user_ref_id="user-1",
                               channel_provider_code="in_app", status="delivered")
        result = retry_worker.retry_if_eligible(recipient, "t", "b", "corr-1")
        self.assertIsNone(result)


class TestChannelHealthCheckWorker(unittest.TestCase):

    def test_healthy_below_threshold(self):
        repo = InMemoryNtfRepository()
        repo._channel_providers["email"] = ChannelProviderInfo(code="email", display_name="Email")
        worker = ChannelHealthCheckWorker(repo, degraded_threshold=3, offline_threshold=6)
        result = worker.evaluate("email", consecutive_failures=1)
        self.assertEqual(result.health_status, "healthy")

    def test_degraded_at_threshold(self):
        repo = InMemoryNtfRepository()
        repo._channel_providers["email"] = ChannelProviderInfo(code="email", display_name="Email")
        worker = ChannelHealthCheckWorker(repo, degraded_threshold=3, offline_threshold=6)
        result = worker.evaluate("email", consecutive_failures=4)
        self.assertEqual(result.health_status, "degraded")

    def test_offline_at_threshold(self):
        repo = InMemoryNtfRepository()
        repo._channel_providers["email"] = ChannelProviderInfo(code="email", display_name="Email")
        worker = ChannelHealthCheckWorker(repo, degraded_threshold=3, offline_threshold=6)
        result = worker.evaluate("email", consecutive_failures=7)
        self.assertEqual(result.health_status, "offline")


if __name__ == "__main__":
    unittest.main(verbosity=2)
