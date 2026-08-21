"""
test_cnt_api.py — اختبارات وحدة لطبقة REST API لخدمة إدارة المحتوى (CNT)
مُحدَّثة لعقد CMS الموسَّع (Master Handoff §8): bilingual، slug، SEO،
category، Draft→Published→Archived.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from cnt_api import router as cnt_router
from cnt_repository import InMemoryCntRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(cnt_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.cnt_repository = InMemoryCntRepository()

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


def _create_payload(**overrides):
    payload = {"title_ar": "خبر مهم", "body_ar": "محتوى المقال بالعربية"}
    payload.update(overrides)
    return payload


class TestCreateArticle:

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.post("/api/v1/content/articles", json=_create_payload())
        assert resp.status_code == 403

    def test_editor_can_create(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor@example.com", role="news_editor")
        resp = client.post("/api/v1/content/articles", json=_create_payload())
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "draft"
        assert body["slug"] == "خبر-مهم" or body["slug"]  # slugify نتاجه عربي مسموح؛ فقط تأكيد عدم الفراغ

    def test_empty_title_ar_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor2@example.com", role="news_editor")
        resp = client.post("/api/v1/content/articles", json=_create_payload(title_ar="  "))
        assert resp.status_code == 400

    def test_explicit_slug_used(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor3@example.com", role="news_editor")
        resp = client.post("/api/v1/content/articles", json=_create_payload(slug="my-custom-slug"))
        assert resp.status_code == 201
        assert resp.json()["slug"] == "my-custom-slug"

    def test_duplicate_slug_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor4@example.com", role="news_editor")
        client.post("/api/v1/content/articles", json=_create_payload(slug="dup-slug"))
        resp = client.post("/api/v1/content/articles", json=_create_payload(slug="dup-slug"))
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "DUPLICATE_SLUG"

    def test_seo_title_too_long_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor5@example.com", role="news_editor")
        resp = client.post(
            "/api/v1/content/articles",
            json=_create_payload(seo_title_ar="س" * 71),
        )
        assert resp.status_code == 422  # Pydantic max_length على مستوى الحقل

    def test_bilingual_fields_persisted(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor6@example.com", role="news_editor")
        resp = client.post(
            "/api/v1/content/articles",
            json=_create_payload(title_en="Important News", body_en="English body",
                                  summary_ar="ملخص", summary_en="Summary"),
        )
        body = resp.json()
        assert body["title_en"] == "Important News"
        assert body["summary_en"] == "Summary"


class TestUpdateArticle:

    def test_editor_can_update_draft(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor7@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        resp = client.put(f"/api/v1/content/articles/{article_id}", json={"title_ar": "عنوان محدَّث"})
        assert resp.status_code == 200
        assert resp.json()["title_ar"] == "عنوان محدَّث"

    def test_update_nonexistent_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor8@example.com", role="news_editor")
        resp = client.put("/api/v1/content/articles/ghost", json={"title_ar": "x"})
        assert resp.status_code == 404

    def test_slug_change_endpoint(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor9@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload(slug="old-slug")).json()["id"]
        resp = client.put(f"/api/v1/content/articles/{article_id}/slug", json={"slug": "new-slug"})
        assert resp.status_code == 200
        assert resp.json()["slug"] == "new-slug"

    def test_slug_change_conflict(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor10@example.com", role="news_editor")
        client.post("/api/v1/content/articles", json=_create_payload(slug="taken-slug"))
        article_id = client.post("/api/v1/content/articles", json=_create_payload(slug="free-slug")).json()["id"]
        resp = client.put(f"/api/v1/content/articles/{article_id}/slug", json={"slug": "taken-slug"})
        assert resp.status_code == 409


class TestStateMachine:

    def test_draft_publish_appears_in_public_list(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorA@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        publish_resp = client.post(f"/api/v1/content/articles/{article_id}/publish")
        assert publish_resp.status_code == 200
        assert publish_resp.json()["status"] == "published"
        assert publish_resp.json()["published_at"] is not None

        client.post("/api/v1/auth/logout")
        resp = client.get("/api/v1/content/articles")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 1

    def test_unpublish_removes_from_public_list(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorB@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post(f"/api/v1/content/articles/{article_id}/publish")
        unpublish_resp = client.post(f"/api/v1/content/articles/{article_id}/unpublish")
        assert unpublish_resp.status_code == 200
        assert unpublish_resp.json()["status"] == "draft"

        resp = client.get("/api/v1/content/articles")
        assert resp.json()["pagination"]["total_items"] == 0

    def test_cannot_publish_already_published(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorC@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post(f"/api/v1/content/articles/{article_id}/publish")
        resp = client.post(f"/api/v1/content/articles/{article_id}/publish")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "INVALID_TRANSITION"

    def test_archive_from_draft(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorD@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        resp = client.post(f"/api/v1/content/articles/{article_id}/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    def test_archive_from_published(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorE@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post(f"/api/v1/content/articles/{article_id}/publish")
        resp = client.post(f"/api/v1/content/articles/{article_id}/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    def test_cannot_publish_archived(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorF@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post(f"/api/v1/content/articles/{article_id}/archive")
        resp = client.post(f"/api/v1/content/articles/{article_id}/publish")
        assert resp.status_code == 409

    def test_non_editor_cannot_publish(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorG@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "buyer2@example.com", role="individual_buyer")
        resp = client.post(f"/api/v1/content/articles/{article_id}/publish")
        assert resp.status_code == 403


class TestGetArticle:

    def test_get_nonexistent_article_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "viewer2@example.com")
        resp = client.get("/api/v1/content/articles/ghost")
        assert resp.status_code == 404

    def test_get_by_slug_draft_returns_404_without_session(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorH@example.com", role="news_editor")
        client.post("/api/v1/content/articles", json=_create_payload(slug="findable"))
        # لم يُنشَر بعد (draft) → 404 لغير المحرر، لا 401 — لا كشف وجود المقال
        client.post("/api/v1/auth/logout")
        resp = client.get("/api/v1/content/articles/slug/findable")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "ARTICLE_NOT_FOUND"

    def test_get_by_slug_404_when_missing(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/content/articles/slug/does-not-exist")
        assert resp.status_code == 404


class TestAdminList:

    def test_admin_list_requires_editor(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer3@example.com", role="individual_buyer")
        resp = client.get("/api/v1/content/articles/admin")
        assert resp.status_code == 403

    def test_admin_list_shows_all_statuses(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorI@example.com", role="news_editor")
        client.post("/api/v1/content/articles", json=_create_payload(slug="a1"))
        a2 = client.post("/api/v1/content/articles", json=_create_payload(slug="a2")).json()["id"]
        client.post(f"/api/v1/content/articles/{a2}/publish")

        resp = client.get("/api/v1/content/articles/admin")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 2

    def test_admin_list_filter_by_status(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorJ@example.com", role="news_editor")
        client.post("/api/v1/content/articles", json=_create_payload(slug="b1"))
        b2 = client.post("/api/v1/content/articles", json=_create_payload(slug="b2")).json()["id"]
        client.post(f"/api/v1/content/articles/{b2}/publish")

        resp = client.get("/api/v1/content/articles/admin", params={"status": "draft"})
        assert resp.json()["pagination"]["total_items"] == 1


class TestArticleVisibilityPolicy:
    """CR-017 + CMS Draft Policy: قائمة المقالات وتفاصيل المقال المنشور
    عامتان بالكامل بلا جلسة. draft/archived مقصور على news_editor حصريًا
    (تصحيح واعٍ عن السلوك القديم — "أي جلسة" لم يعد يناسب نموذج CMS)."""

    def test_list_articles_no_session_required(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/content/articles")
        assert resp.status_code == 200

    def test_published_article_readable_without_session(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorK@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post(f"/api/v1/content/articles/{article_id}/publish")
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/content/articles/{article_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"

    def test_unpublished_article_returns_404_without_session(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editorL@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/content/articles/{article_id}")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "ARTICLE_NOT_FOUND"

    def test_unpublished_article_returns_404_for_non_editor_session(self, app_and_client):
        """CMS الجديد: draft ليس محتوى عامًا لأي مستخدم مسجَّل — محرر
        الأخبار فقط. سلوك مغاير عمدًا عن REQ-CNT القديم قبل CMS."""
        app, client = app_and_client
        _login_as(app, client, "editorM@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "randombuyer@example.com", role="individual_buyer")
        resp = client.get(f"/api/v1/content/articles/{article_id}")
        assert resp.status_code == 404

    def test_unpublished_article_readable_by_any_news_editor(self, app_and_client):
        """أي news_editor يرى أي draft — لا نموذج ملكية فردية (نفس منطق
        publish/unpublish/archive الحالي، لا نخترع تشددًا إضافيًا هنا)."""
        app, client = app_and_client
        _login_as(app, client, "editorN1@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json=_create_payload()).json()["id"]
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "editorN2@example.com", role="news_editor")
        resp = client.get(f"/api/v1/content/articles/{article_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "draft"
