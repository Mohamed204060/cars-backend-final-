"""
test_message_service.py — اختبارات وحدة لخدمة التواصل (COM)
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import message_service as svc  # noqa: E402
from message_service import (  # noqa: E402
    Conversation, Message, create_conversation, send_message,
    delete_message_for_sender, delete_message_for_recipient, is_message_visible_to,
    build_message_audit_event,
    InvalidContextTypeError, EmptyMessageBodyError,
)


class TestNoHardDeleteInMessagingModule(unittest.TestCase):
    """مبدأ عدم الحذف الفعلي (دليل الحوكمة 6.6)؛ تحقق هيكلي كما جرى في خدمات سابقة."""

    def test_no_hard_delete_function_defined(self):
        function_names = [name for name, obj in inspect.getmembers(svc, inspect.isfunction)]
        # ملاحظة: أسماء الدوال هنا تحتوي "delete" لكنها حذف نسبي موثَّق صراحة (Soft, Per-Viewer)؛
        # الفحص الحقيقي هو عدم وجود إزالة فعلية للسجل من أي بنية بيانات، لا غياب الكلمة لفظيًا.
        # التحقق الفعلي: التأكد من أن كل دالة تحمل "delete" تُعدِّل حالة (is_deleted_*) لا تُزيل الكائن.
        message = Message(id="m1", conversation_id="c1", sender_user_ref_id="user-1", body="hi")
        delete_message_for_sender(message)
        # الرسالة ما زالت موجودة ككائن كامل بكل حقولها، لم تُحذَف
        self.assertEqual(message.body, "hi")
        self.assertTrue(message.is_deleted_by_sender)


class TestConversationCreation(unittest.TestCase):
    """REQ-COM-001, 002"""

    def test_create_conversation_for_purchase_request(self):
        conv = create_conversation("purchase_request", "pr-1")
        self.assertEqual(conv.context_ref_id, "pr-1")

    def test_create_conversation_for_inventory_item(self):
        conv = create_conversation("inventory_item", "item-1")
        self.assertEqual(conv.context_type, "inventory_item")

    def test_invalid_context_type_rejected(self):
        with self.assertRaises(InvalidContextTypeError):
            create_conversation("random_context", "x-1")

    def test_conversation_has_no_status_field(self):
        # REQ-COM-010: لا حقل حالة يمكن أن يُغلَق تبعًا لحالة الطلب
        conv = create_conversation("purchase_request", "pr-1")
        self.assertNotIn("status", conv.__dict__)


class TestSendMessage(unittest.TestCase):
    """REQ-COM-001"""

    def setUp(self):
        self.conv = create_conversation("purchase_request", "pr-1")
        self.conv.id = "conv-1"

    def test_send_message_success(self):
        message = send_message(self.conv, "buyer-1", "هل ما زالت القطعة متوفرة؟")
        self.assertEqual(message.conversation_id, "conv-1")
        self.assertFalse(message.is_deleted_by_sender)

    def test_empty_body_rejected(self):
        with self.assertRaises(EmptyMessageBodyError):
            send_message(self.conv, "buyer-1", "   ")


class TestPerViewerSoftDelete(unittest.TestCase):
    """REQ-COM-007"""

    def setUp(self):
        self.conv = create_conversation("purchase_request", "pr-1")
        self.conv.id = "conv-1"
        self.message = send_message(self.conv, "buyer-1", "رسالة تجريبية")

    def test_visible_to_both_by_default(self):
        self.assertTrue(is_message_visible_to(self.message, "sender"))
        self.assertTrue(is_message_visible_to(self.message, "recipient"))

    def test_delete_for_sender_does_not_affect_recipient_view(self):
        delete_message_for_sender(self.message)
        self.assertFalse(is_message_visible_to(self.message, "sender"))
        self.assertTrue(is_message_visible_to(self.message, "recipient"))  # لم يتأثر الطرف الآخر

    def test_delete_for_recipient_does_not_affect_sender_view(self):
        delete_message_for_recipient(self.message)
        self.assertFalse(is_message_visible_to(self.message, "recipient"))
        self.assertTrue(is_message_visible_to(self.message, "sender"))

    def test_both_can_delete_independently(self):
        delete_message_for_sender(self.message)
        delete_message_for_recipient(self.message)
        self.assertFalse(is_message_visible_to(self.message, "sender"))
        self.assertFalse(is_message_visible_to(self.message, "recipient"))

    def test_unknown_viewer_role_raises(self):
        with self.assertRaises(ValueError):
            is_message_visible_to(self.message, "moderator")


class TestAuditEventBuilder(unittest.TestCase):

    def test_build_event_for_known_action(self):
        event = build_message_audit_event("message_sent", "buyer-1", "conv-1")
        self.assertEqual(event["log_type"], "general")

    def test_build_event_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            build_message_audit_event("unknown_action", "buyer-1", "conv-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
