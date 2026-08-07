"""
cnt_api.py — طبقة REST API لخدمة إدارة المحتوى (CNT)
المرجع: REQ-CNT-001، 002

الصلاحية: إنشاء/نشر/إلغاء نشر مقال — دور "محرر الأخبار" (news_editor) حصريًا.
عرض المقالات المنشورة — عام، أي جلسة صالحة.
"""

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from session_service import Session
from cnt_service import EmptyArticleFieldError, create_article_via_repository, publish_article_via_repository, unpublish_article_via_repository

router = APIRouter(prefix="/api/v1/content/articles", tags=["content"])

NEWS_EDITOR_ROLES = {"news_editor"}


class ArticleCreateRequest(BaseModel):
    title: str
    body: str


class ArticleResponse(BaseModel):
    id: str
    author_ref_id: str
    title: str
    body: str
    status: str


def get_cnt_repository(request: Request):
    return request.app.state.cnt_repository


def get_auth_repository_for_editor_check(request: Request):
    return request.app.state.auth_repository


def _to_response(article) -> ArticleResponse:
    return ArticleResponse(id=article.id, author_ref_id=article.author_ref_id,
                            title=article.title, body=article.body, status=article.status)


def _ensure_news_editor(correlation_id, auth_repo, user_id):
    role = auth_repo.get_user_role(user_id)
    if role not in NEWS_EDITOR_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية مقصورة على محرر الأخبار.")


@router.post("", response_model=ArticleResponse, status_code=status.HTTP_201_CREATED)
def create_article(
    body: ArticleCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    _ensure_news_editor(correlation_id, auth_repo, current_session.user_id)
    try:
        article = create_article_via_repository(cnt_repo, author_ref_id=current_session.user_id,
                                                  title=body.title, body=body.body)
    except EmptyArticleFieldError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_ARTICLE_FIELD", str(exc))
    return _to_response(article)


@router.get("", response_model=list[ArticleResponse])
def list_published_articles(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
):
    return [_to_response(a) for a in cnt_repo.get_published_articles()]


@router.get("/{article_id}", response_model=ArticleResponse)
def get_article(
    article_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
):
    article = cnt_repo.get_article_by_id(article_id)
    if article is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", "المقال غير موجود.")
    return _to_response(article)


@router.post("/{article_id}/publish", response_model=ArticleResponse)
def publish_article(
    article_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    _ensure_news_editor(correlation_id, auth_repo, current_session.user_id)
    try:
        article = publish_article_via_repository(cnt_repo, article_id=article_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", str(exc))
    return _to_response(article)


@router.post("/{article_id}/unpublish", response_model=ArticleResponse)
def unpublish_article(
    article_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    _ensure_news_editor(correlation_id, auth_repo, current_session.user_id)
    try:
        article = unpublish_article_via_repository(cnt_repo, article_id=article_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", str(exc))
    return _to_response(article)
