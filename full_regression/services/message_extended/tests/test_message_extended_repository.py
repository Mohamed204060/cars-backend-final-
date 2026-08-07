"""
test_message_extended_repository.py — اختبارات Repository لتوسعة خدمة التواصل
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from message_extended_service import (  # noqa: E402
    go_online_via_repository, go_offline_via_repository,
    mark_delivered_via_repository, mark_read_via_repository,
    add_attachment_via_repository, mute_conversation_via_repository,
    archive_conversation_for_user_via_repository,
    InvalidDeliveryTrackingTransitionError, AttachmentRejectedError,
)
from message_extended_repository import InMemoryMessageExtendedRepository  # noqa: E402


class TestPresenceViaRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryMessageExtendedRepository()

    def test_go_online_then_offline(self):
        go_online_via_repository(self.repo, "user-1")
        fetched = self.repo.get_presence("user-1")
        self.assertTrue(fetched.is_online)

        go_offline_via_repository(self.repo, "user-1", datetime(2026, 1, 1, 9))
        fetched2 = self.repo.get_presence("user-1")
        self.assertFalse(fetched2.is_online)
        self.assertEqual(fetched2.last_seen_at, datetime(2026, 1, 1, 9))


class TestDeliveryTrackingViaRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryMessageExtendedRepository()

    def test_mark_delivered_then_read_persists(self):
        mark_delivered_via_repository(self.repo, "msg-1", sent_at=datetime(2026, 1, 1, 10),
                                       occurred_at=datetime(2026, 1, 1, 10, 1))
        mark_read_via_repository(self.repo, "msg-1", occurred_at=datetime(2026, 1, 1, 10, 5))

        fetched = self.repo.get_delivery_tracking("msg-1")
        self.assertIsNotNone(fetched.delivered_at)
        self.assertIsNotNone(fetched.read_at)

    def test_mark_read_without_tracking_raises(self):
        with self.assertRaises(ValueError):
            mark_read_via_repository(self.repo, "nonexistent", occurred_at=datetime(2026, 1, 1))

    def test_double_delivered_via_repository_raises(self):
        mark_delivered_via_repository(self.repo, "msg-1", sent_at=datetime(2026, 1, 1), occurred_at=datetime(2026, 1, 1, 1))
        with self.assertRaises(InvalidDeliveryTrackingTransitionError):
            mark_delivered_via_repository(self.repo, "msg-1", sent_at=datetime(2026, 1, 1), occurred_at=datetime(2026, 1, 1, 2))


class TestAttachmentViaRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryMessageExtendedRepository()

    def test_add_valid_attachment(self):
        att = add_attachment_via_repository(self.repo, "msg-1", "photo.png", "image/png", 100_000)
        self.assertTrue(att.id.startswith("attachment-"))
        fetched = self.repo.get_attachments_for_message("msg-1")
        self.assertEqual(len(fetched), 1)

    def test_invalid_attachment_rejected_not_persisted(self):
        with self.assertRaises(AttachmentRejectedError):
            add_attachment_via_repository(self.repo, "msg-1", "bad.exe", "application/octet-stream", 100)
        fetched = self.repo.get_attachments_for_message("msg-1")
        self.assertEqual(len(fetched), 0)


class TestConversationSettingsViaRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryMessageExtendedRepository()

    def test_mute_persists_independent_per_user(self):
        mute_conversation_via_repository(self.repo, "conv-1", "user-1")

        fetched_user1 = self.repo.get_conversation_user_settings("conv-1", "user-1")
        fetched_user2 = self.repo.get_conversation_user_settings("conv-1", "user-2")
        self.assertTrue(fetched_user1.is_muted)
        self.assertIsNone(fetched_user2)  # الطرف الآخر غير متأثر إطلاقًا

    def test_archive_for_one_user_does_not_archive_for_other(self):
        archive_conversation_for_user_via_repository(self.repo, "conv-1", "user-1")

        fetched_user1 = self.repo.get_conversation_user_settings("conv-1", "user-1")
        self.assertTrue(fetched_user1.is_archived)


if __name__ == "__main__":
    unittest.main(verbosity=2)
