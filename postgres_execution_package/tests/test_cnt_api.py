"""
test_cnt_api.py — اختبارات وحدة لطبقة REST API لخدمة إدارة المحتوى (CNT)
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


class TestCreateArticle:

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.post("/api/v1/content/articles", json={"title": "خبر", "body": "محتوى"})
        assert resp.status_code == 403

    def test_editor_can_create(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor@example.com", role="news_editor")
        resp = client.post("/api/v1/content/articles", json={"title": "خبر", "body": "محتوى"})
        assert resp.status_code == 201
        assert resp.json()["status"] == "unpublished"

    def test_empty_title_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor2@example.com", role="news_editor")
        resp = client.post("/api/v1/content/articles", json={"title": "  ", "body": "محتوى"})
        assert resp.status_code == 400


class TestPublishUnpublish:

    def test_unpublished_article_not_in_public_list(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor3@example.com", role="news_editor")
        client.post("/api/v1/content/articles", json={"title": "غير منشور", "body": "x"})

        resp = client.get("/api/v1/content/articles")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_publish_then_appears_in_public_list(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor4@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json={"title": "خبر مهم", "body": "x"}).json()["id"]
        publish_resp = client.post(f"/api/v1/content/articles/{article_id}/publish")
        assert publish_resp.status_code == 200
        assert publish_resp.json()["status"] == "published"

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "viewer@example.com", role="individual_buyer")
        list_resp = client.get("/api/v1/content/articles")
        assert len(list_resp.json()) == 1

    def test_unpublish_removes_from_public_list(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor5@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json={"title": "خبر", "body": "x"}).json()["id"]
        client.post(f"/api/v1/content/articles/{article_id}/publish")
        unpublish_resp = client.post(f"/api/v1/content/articles/{article_id}/unpublish")
        assert unpublish_resp.status_code == 200
        assert unpublish_resp.json()["status"] == "unpublished"

        resp = client.get("/api/v1/content/articles")
        assert resp.json() == []

    def test_non_editor_cannot_publish(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "editor6@example.com", role="news_editor")
        article_id = client.post("/api/v1/content/articles", json={"title": "خبر", "body": "x"}).json()["id"]

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
