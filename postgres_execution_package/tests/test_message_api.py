"""
test_message_api.py — اختبارات وحدة لطبقة REST API لخدمة التواصل (COM)
وامتدادها (المرفقات).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from message_api import router as message_router
from message_repository import InMemoryMessageRepository
from message_extended_api import router as message_extended_router
from message_extended_repository import InMemoryMessageExtendedRepository
from order_api import router as order_router
from order_repository import InMemoryOrderRepository
from ref_repository import InMemoryRefRepository
from vct_repository import InMemoryVctRepository
from store_api import router as store_router
from store_repository import InMemoryStoreRepository
from inventory_item_api import router as inventory_router
from inventory_item_repository import InMemoryInventoryItemRepository
from aud_repository import InMemoryAudRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(message_router)
    app.include_router(message_extended_router)
    app.include_router(order_router)
    app.include_router(store_router)
    app.include_router(inventory_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.aud_repository = InMemoryAudRepository()
    app.state.message_repository = InMemoryMessageRepository()
    app.state.message_extended_repository = InMemoryMessageExtendedRepository()
    # CR-015: message_api.py send_message يستدعي _resolve_canonical_participant
    # الذي يحقن order_repo/store_repo/inventory_repo — مطلوبة هنا حتى لو لم
    # تختبر هذه الحزمة سيناريوهات purchase_request/inventory_item مباشرة.
    app.state.order_repository = InMemoryOrderRepository()
    # CR-022: order_api.create_purchase_request يعتمد الآن على ref_repository
    # أيضًا (تحقق condition_ref_id) — order_router مُسجَّل في هذا التطبيق،
    # فتُهيَّأ وقائيًا بنفس منطق order_repository أعلاه، رغم أن هذه الحزمة
    # لا تختبر مسار الإنشاء مباشرة.
    app.state.ref_repository = InMemoryRefRepository()
    app.state.vct_repository = InMemoryVctRepository()  # Batch 1: order_api.create_purchase_request يعتمد عليه الآن (لا اختبار هنا يستدعيه فعليًا، بنفس نمط ref_repository سابقًا)
    app.state.store_repository = InMemoryStoreRepository()
    app.state.inventory_repository = InMemoryInventoryItemRepository()

    client = TestClient(app, base_url="https://testserver")
    return app, client


def _login_as(app, client, email: str, role: str = "individual_buyer") -> str:
    repo = app.state.auth_repository
    user_id = repo.create_user()
    repo.set_user_role(user_id, role)
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password="Str0ngPass1!")
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200
    return user_id


class TestSendMessage:

    def test_send_message_success(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender@example.com")

        resp = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-1", "body": "هل السعر قابل للتفاوض؟",
        })
        assert resp.status_code == 201
        assert resp.json()["body"] == "هل السعر قابل للتفاوض؟"

    def test_invalid_context_type_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender2@example.com")

        resp = client.post("/api/v1/messages", json={
            "context_type": "not_a_real_context", "context_ref_id": "x", "body": "test",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_CONTEXT_TYPE"

    def test_empty_body_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender3@example.com")

        resp = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-1", "body": "   ",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "EMPTY_MESSAGE_BODY"

    def test_second_message_same_context_reuses_conversation(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender4@example.com")

        first = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-shared", "body": "رسالة أولى",
        })
        second = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-shared", "body": "رسالة ثانية",
        })
        assert first.json()["conversation_id"] == second.json()["conversation_id"]


class TestListAndDeleteMessages:

    def test_list_messages_in_conversation(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender5@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-list", "body": "رسالة",
        }).json()

        resp = client.get(f"/api/v1/conversations/{sent['conversation_id']}/messages")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_sender_deletes_message_relatively_not_actually(self, app_and_client):
        """REQ-COM-007: حذف نسبي فقط، لا فعلي — يبقى السجل موجودًا في المستودع."""
        app, client = app_and_client
        _login_as(app, client, "sender6@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-del", "body": "سيُحذَف نسبيًا",
        }).json()

        delete_resp = client.delete(f"/api/v1/conversations/{sent['conversation_id']}/messages/{sent['id']}")
        assert delete_resp.status_code == 200
        assert delete_resp.json()["is_deleted_by_sender"] is True

        # لا يزال موجودًا فعليًا في المستودع (لا حذف حقيقي)
        stored = app.state.message_repository.get_messages_for_conversation(sent["conversation_id"])
        assert len(stored) == 1
        assert stored[0].id == sent["id"]

    def test_delete_nonexistent_message_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender7@example.com")
        resp = client.delete("/api/v1/conversations/some-conv/messages/ghost")
        assert resp.status_code == 404


class TestAttachments:

    def test_add_valid_attachment(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender8@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-att", "body": "انظر المرفق",
        }).json()

        resp = client.post(f"/api/v1/messages/{sent['id']}/attachments", json={
            "file_name": "photo.jpg", "mime_type": "image/jpeg", "size_bytes": 50_000,
        })
        assert resp.status_code == 201
        assert resp.json()["file_name"] == "photo.jpg"

    def test_forbidden_extension_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender9@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-att2", "body": "مرفق خطر",
        }).json()

        resp = client.post(f"/api/v1/messages/{sent['id']}/attachments", json={
            "file_name": "virus.exe", "mime_type": "image/jpeg", "size_bytes": 1000,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "ATTACHMENT_REJECTED"

    def test_oversized_attachment_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender10@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-att3", "body": "مرفق كبير",
        }).json()

        resp = client.post(f"/api/v1/messages/{sent['id']}/attachments", json={
            "file_name": "huge.pdf", "mime_type": "application/pdf", "size_bytes": 20 * 1024 * 1024,
        })
        assert resp.status_code == 400

class TestAdminPrivilegedMessageContentAccess:
    """تصحيح توازٍ (Parity): تغطية InMemory إلزامية لمسار محتوى الرسائل
    الإداري المميَّز (GET /admin/conversations/{id}/messages) — لم تكن
    موجودة إطلاقًا سابقًا؛ الخطر الحقيقي المُكتشَف: with aud_repo.connection:
    في message_api.py كانت ستفشل بـAttributeError على InMemoryAudRepository
    (بلا خاصية connection) لولا إضافتها الآن (aud_repository.py). هذا
    الاختبار يُثبِت أن المسار يعمل فعليًا على مسار الاختبار الوهمي المستخدَم
    في بقية هذا الملف، لا PostgreSQL فقط."""

    def test_super_admin_can_read_full_content_including_deleted(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender-admin-test@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-admin-1", "body": "محتوى حسّاس",
        }).json()
        conversation_id = sent["conversation_id"]

        client.delete(f"/api/v1/conversations/{conversation_id}/messages/{sent['id']}")

        _login_as(app, client, "root-admin-test@example.com", role="super_admin")
        resp = client.get(f"/api/v1/admin/conversations/{conversation_id}/messages")
        assert resp.status_code == 200, resp.text
        bodies = [m["body"] for m in resp.json()]
        assert "محتوى حسّاس" in bodies

    def test_regular_admin_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "regular-admin-test@example.com", role="admin")
        resp = client.get("/api/v1/admin/conversations/some-conversation-id/messages")
        assert resp.status_code == 403

    def test_creates_audit_event_without_body_content(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "sender-admin-test2@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-admin-2", "body": "نص لا يجب تسريبه",
        }).json()
        conversation_id = sent["conversation_id"]

        admin_id = _login_as(app, client, "root-admin-test2@example.com", role="super_admin")
        resp = client.get(f"/api/v1/admin/conversations/{conversation_id}/messages")
        assert resp.status_code == 200

        events = [e for e in app.state.aud_repository._events if e.event_name == "admin_message_content_accessed"]
        assert len(events) == 1
        assert events[0].actor_ref_id == admin_id
        assert events[0].log_type == "administrative"
        assert events[0].metadata.get("conversation_id") == conversation_id
        assert "نص لا يجب تسريبه" not in str(events[0].metadata)

    def test_audit_persistence_failure_returns_503_not_content(self, app_and_client):
        """يثبت أن with aud_repo.connection: يعمل فعليًا مع InMemoryAudRepository
        (لا AttributeError)، وأن فشل التدقيق (حتى لو Python-level هنا) يمنع
        إعادة أي محتوى حسّاس — بلا تسريب تفاصيل الاستثناء الخام في الاستجابة."""
        app, client = app_and_client
        _login_as(app, client, "sender-admin-test3@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-admin-3", "body": "محتوى آخر",
        }).json()
        conversation_id = sent["conversation_id"]
        _login_as(app, client, "root-admin-test3@example.com", role="super_admin")

        def _broken_insert(event):
            raise RuntimeError("simulated aud outage with internal detail: table aud.events column xyz")
        app.state.aud_repository.insert_event = _broken_insert

        resp = client.get(f"/api/v1/admin/conversations/{conversation_id}/messages")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "MESSAGE_ACCESS_AUDIT_PERSISTENCE_FAILED"
        assert "محتوى آخر" not in resp.text
        # تسريب: لا تفاصيل الاستثناء الخام (نص محاكاة الخطأ الداخلي) في الاستجابة العامة
        assert "table aud.events column xyz" not in resp.text
