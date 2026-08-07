"""
test_message_extended_service.py — اختبارات وحدة لتوسعة خدمة التواصل
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
from datetime import datetime
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from message_extended_service import (  # noqa: E402
    build_typing_signal, UserPresence, mark_user_online, mark_user_offline,
    MessageDeliveryTracking, mark_delivered, mark_read, InvalidDeliveryTrackingTransitionError,
    create_reply_link, SelfReplyError, create_forward_record,
    validate_and_create_attachment, AttachmentRejectedError,
    search_messages_by_text,
    ConversationUserSettings, mute_conversation, unmute_conversation,
    archive_conversation_for_user, unarchive_conversation_for_user,
    build_message_or_conversation_report_event,
)


class TestTypingIndicator(unittest.TestCase):

    def test_build_typing_signal_true(self):
        signal = build_typing_signal("conv-1", "user-1", True)
        self.assertTrue(signal["is_typing"])

    def test_build_typing_signal_false(self):
        signal = build_typing_signal("conv-1", "user-1", False)
        self.assertFalse(signal["is_typing"])


class TestPresence(unittest.TestCase):

    def test_mark_online(self):
        presence = UserPresence(user_ref_id="user-1")
        mark_user_online(presence)
        self.assertTrue(presence.is_online)

    def test_mark_offline_records_last_seen(self):
        presence = UserPresence(user_ref_id="user-1", is_online=True)
        mark_user_offline(presence, datetime(2026, 1, 1, 12, 0))
        self.assertFalse(presence.is_online)
        self.assertEqual(presence.last_seen_at, datetime(2026, 1, 1, 12, 0))


class TestDeliveryTracking(unittest.TestCase):
    """REQ-COM-015, 031"""

    def test_mark_delivered_then_read(self):
        tracking = MessageDeliveryTracking(message_id="msg-1", sent_at=datetime(2026, 1, 1, 10))
        mark_delivered(tracking, datetime(2026, 1, 1, 10, 1))
        mark_read(tracking, datetime(2026, 1, 1, 10, 5))
        self.assertIsNotNone(tracking.delivered_at)
        self.assertIsNotNone(tracking.read_at)

    def test_cannot_read_before_delivered(self):
        tracking = MessageDeliveryTracking(message_id="msg-1", sent_at=datetime(2026, 1, 1, 10))
        with self.assertRaises(InvalidDeliveryTrackingTransitionError):
            mark_read(tracking, datetime(2026, 1, 1, 10, 5))

    def test_cannot_double_mark_delivered(self):
        tracking = MessageDeliveryTracking(message_id="msg-1", sent_at=datetime(2026, 1, 1, 10))
        mark_delivered(tracking, datetime(2026, 1, 1, 10, 1))
        with self.assertRaises(InvalidDeliveryTrackingTransitionError):
            mark_delivered(tracking, datetime(2026, 1, 1, 10, 2))

    def test_cannot_double_mark_read(self):
        tracking = MessageDeliveryTracking(message_id="msg-1", sent_at=datetime(2026, 1, 1, 10))
        mark_delivered(tracking, datetime(2026, 1, 1, 10, 1))
        mark_read(tracking, datetime(2026, 1, 1, 10, 5))
        with self.assertRaises(InvalidDeliveryTrackingTransitionError):
            mark_read(tracking, datetime(2026, 1, 1, 10, 6))


class TestReplyAndForward(unittest.TestCase):

    def test_create_reply_link(self):
        link = create_reply_link("msg-2", "msg-1")
        self.assertEqual(link.reply_to_message_id, "msg-1")

    def test_self_reply_rejected(self):
        with self.assertRaises(SelfReplyError):
            create_reply_link("msg-1", "msg-1")

    def test_create_forward_record(self):
        record = create_forward_record("msg-1", "msg-2", "conv-9")
        self.assertEqual(record.forwarded_to_conversation_id, "conv-9")


class TestAttachmentValidation(unittest.TestCase):
    """REQ-COM-016"""

    def test_valid_image_attachment_accepted(self):
        att = validate_and_create_attachment("msg-1", "photo.jpg", "image/jpeg", 500_000)
        self.assertEqual(att.file_name, "photo.jpg")

    def test_executable_extension_rejected(self):
        with self.assertRaises(AttachmentRejectedError):
            validate_and_create_attachment("msg-1", "virus.exe", "application/octet-stream", 1000)

    def test_shell_script_extension_rejected(self):
        with self.assertRaises(AttachmentRejectedError):
            validate_and_create_attachment("msg-1", "script.sh", "text/plain", 1000)

    def test_disallowed_mime_type_rejected(self):
        with self.assertRaises(AttachmentRejectedError):
            validate_and_create_attachment("msg-1", "data.zip", "application/zip", 1000)

    def test_oversized_file_rejected(self):
        with self.assertRaises(AttachmentRejectedError):
            validate_and_create_attachment("msg-1", "huge.pdf", "application/pdf", 999_999_999)

    def test_zero_size_rejected(self):
        with self.assertRaises(AttachmentRejectedError):
            validate_and_create_attachment("msg-1", "empty.png", "image/png", 0)

    def test_mismatched_extension_disguising_executable_still_checked_by_mime(self):
        # امتداد صورة لكن نوع محتوى غير مسموح: يُرفَض عبر فحص MIME لا الامتداد وحده
        with self.assertRaises(AttachmentRejectedError):
            validate_and_create_attachment("msg-1", "fake.jpg", "application/x-msdownload", 1000)


class TestMessageSearch(unittest.TestCase):

    def setUp(self):
        @dataclass
        class FakeMessage:
            body: str
        self.FakeMessage = FakeMessage
        self.messages = [FakeMessage(body="هل القطعة متوفرة؟"), FakeMessage(body="نعم متوفرة الآن"),
                          FakeMessage(body="شكرًا جزيلاً")]

    def test_search_finds_matching_messages(self):
        results = search_messages_by_text(self.messages, "متوفرة")
        self.assertEqual(len(results), 2)

    def test_search_case_and_whitespace_insensitive_query(self):
        results = search_messages_by_text(self.messages, "  متوفرة  ")
        self.assertEqual(len(results), 2)

    def test_empty_query_returns_empty(self):
        results = search_messages_by_text(self.messages, "")
        self.assertEqual(results, [])

    def test_no_match_returns_empty(self):
        results = search_messages_by_text(self.messages, "غير موجود إطلاقًا")
        self.assertEqual(results, [])


class TestConversationUserSettings(unittest.TestCase):

    def test_mute_and_unmute(self):
        settings = ConversationUserSettings(conversation_id="conv-1", user_ref_id="user-1")
        mute_conversation(settings)
        self.assertTrue(settings.is_muted)
        unmute_conversation(settings)
        self.assertFalse(settings.is_muted)

    def test_archive_and_unarchive_does_not_affect_mute(self):
        settings = ConversationUserSettings(conversation_id="conv-1", user_ref_id="user-1")
        mute_conversation(settings)
        archive_conversation_for_user(settings)
        self.assertTrue(settings.is_archived)
        self.assertTrue(settings.is_muted)  # مستقل تمامًا
        unarchive_conversation_for_user(settings)
        self.assertFalse(settings.is_archived)
        self.assertTrue(settings.is_muted)  # لم يتأثر


class TestReporting(unittest.TestCase):

    def test_report_message(self):
        event = build_message_or_conversation_report_event("message", "msg-1", "user-2", "محتوى مسيء")
        self.assertEqual(event["report_target_type"], "message")

    def test_report_conversation(self):
        event = build_message_or_conversation_report_event("conversation", "conv-1", "user-2", "مضايقة")
        self.assertEqual(event["report_target_type"], "conversation")

    def test_invalid_target_type_rejected(self):
        with self.assertRaises(ValueError):
            build_message_or_conversation_report_event("user", "user-1", "user-2", "سبب")

    def test_empty_reason_rejected(self):
        with self.assertRaises(ValueError):
            build_message_or_conversation_report_event("message", "msg-1", "user-2", "   ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
