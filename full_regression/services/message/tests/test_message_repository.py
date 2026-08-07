"""
test_message_repository.py — اختبارات وحدة لتنسيق خدمة التواصل عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from message_service import (  # noqa: E402
    get_or_create_conversation_via_repository, send_message_via_repository,
    delete_message_for_sender_via_repository, delete_message_for_recipient_via_repository,
    is_message_visible_to,
)
from message_repository import InMemoryMessageRepository  # noqa: E402


class TestMessageRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryMessageRepository()

    def test_get_or_create_conversation_reuses_existing(self):
        conv1 = get_or_create_conversation_via_repository(self.repo, "purchase_request", "pr-1")
        conv2 = get_or_create_conversation_via_repository(self.repo, "purchase_request", "pr-1")
        self.assertEqual(conv1.id, conv2.id)  # نفس المحادثة، لا تكرار

    def test_different_context_creates_different_conversation(self):
        conv1 = get_or_create_conversation_via_repository(self.repo, "purchase_request", "pr-1")
        conv2 = get_or_create_conversation_via_repository(self.repo, "purchase_request", "pr-2")
        self.assertNotEqual(conv1.id, conv2.id)

    def test_send_message_via_repository_reuses_conversation(self):
        msg1 = send_message_via_repository(self.repo, "purchase_request", "pr-1", "buyer-1", "أول رسالة")
        msg2 = send_message_via_repository(self.repo, "purchase_request", "pr-1", "seller-1", "رد البائع")
        self.assertEqual(msg1.conversation_id, msg2.conversation_id)

    def test_delete_for_sender_via_repository_persists(self):
        msg = send_message_via_repository(self.repo, "purchase_request", "pr-1", "buyer-1", "رسالة")
        delete_message_for_sender_via_repository(self.repo, msg.id, msg.conversation_id)

        fetched = repo_msg = self.repo.get_messages_for_conversation(msg.conversation_id)[0]
        self.assertFalse(is_message_visible_to(fetched, "sender"))
        self.assertTrue(is_message_visible_to(fetched, "recipient"))  # لم يتأثر الطرف الآخر

    def test_delete_for_recipient_via_repository_persists(self):
        msg = send_message_via_repository(self.repo, "purchase_request", "pr-1", "buyer-1", "رسالة")
        delete_message_for_recipient_via_repository(self.repo, msg.id, msg.conversation_id)

        fetched = self.repo.get_messages_for_conversation(msg.conversation_id)[0]
        self.assertFalse(is_message_visible_to(fetched, "recipient"))
        self.assertTrue(is_message_visible_to(fetched, "sender"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
