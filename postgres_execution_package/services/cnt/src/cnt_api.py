"""
cnt_api.py — طبقة REST API لخدمة إدارة المحتوى (CNT) — CMS خفيف
المرجع: REQ-CNT-001، 002 + Master Handoff §8 (Content Management/Articles)

الصلاحية: إنشاء/تعديل/نشر/إلغاء نشر/أرشفة/تغيير slug مقال — دور
"محرر الأخبار" (news_editor) حصريًا. عرض المقالات المنشورة — عام بالكامل
(CR-017)، بلا جلسة.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from auth_api import error, get_correlation_id, get_current_session, get_optional_session
from session_service import Session
from cnt_service import (
    Article,
    DuplicateSlugError,
    EmptyArticleFieldError,
    InvalidSlugError,
    InvalidStatusTransitionError,
    SeoFieldTooLongError,
    archive_article_via_repository,
    change_slug_via_repository,
    create_article_via_repository,
    publish_article_via_repository,
    unpublish_article_via_repository,
    update_article_via_repository,
)

router = APIRouter(prefix="/api/v1/content/articles", tags=["content"])

NEWS_EDITOR_ROLES = {"news_editor"}


class ArticleCreateRequest(BaseModel):
    title_ar: str
    body_ar: str
    title_en: Optional[str] = None
    body_en: Optional[str] = None
    summary_ar: Optional[str] = None
    summary_en: Optional[str] = None
    slug: Optional[str] = None
    category_ref_id: Optional[str] = None
    seo_title_ar: Optional[str] = Field(default=None, max_length=70)
    seo_title_en: Optional[str] = Field(default=None, max_length=70)
    seo_description_ar: Optional[str] = Field(default=None, max_length=160)
    seo_description_en: Optional[str] = Field(default=None, max_length=160)


class ArticleUpdateRequest(BaseModel):
    title_ar: Optional[str] = None
    body_ar: Optional[str] = None
    title_en: Optional[str] = None
    body_en: Optional[str] = None
    summary_ar: Optional[str] = None
    summary_en: Optional[str] = None
    category_ref_id: Optional[str] = None
    seo_title_ar: Optional[str] = Field(default=None, max_length=70)
    seo_title_en: Optional[str] = Field(default=None, max_length=70)
    seo_description_ar: Optional[str] = Field(default=None, max_length=160)
    seo_description_en: Optional[str] = Field(default=None, max_length=160)


class SlugUpdateRequest(BaseModel):
    slug: str


class ArticleResponse(BaseModel):
    id: str
    author_ref_id: str
    title_ar: str
    body_ar: str
    status: str
    title_en: Optional[str] = None
    body_en: Optional[str] = None
    summary_ar: Optional[str] = None
    summary_en: Optional[str] = None
    slug: str
    category_ref_id: Optional[str] = None
    seo_title_ar: Optional[str] = None
    seo_title_en: Optional[str] = None
    seo_description_ar: Optional[str] = None
    seo_description_en: Optional[str] = None
    published_at: Optional[str] = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class ArticleListResponse(BaseModel):
    results: list[ArticleResponse]
    pagination: PaginationMeta


def get_cnt_repository(request: Request):
    return request.app.state.cnt_repository


def get_auth_repository_for_editor_check(request: Request):
    return request.app.state.auth_repository


def _to_response(article: Article) -> ArticleResponse:
    return ArticleResponse(
        id=article.id, author_ref_id=article.author_ref_id, title_ar=article.title_ar,
        body_ar=article.body_ar, status=article.status, title_en=article.title_en,
        body_en=article.body_en, summary_ar=article.summary_ar, summary_en=article.summary_en,
        slug=article.slug, category_ref_id=article.category_ref_id,
        seo_title_ar=article.seo_title_ar, seo_title_en=article.seo_title_en,
        seo_description_ar=article.seo_description_ar, seo_description_en=article.seo_description_en,
        published_at=article.published_at.isoformat() if article.published_at else None,
    )


def _ensure_news_editor(correlation_id, auth_repo, user_id):
    role = auth_repo.get_user_role(user_id)
    if role not in NEWS_EDITOR_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية مقصورة على محرر الأخبار.")


def _is_news_editor(auth_repo, session: Session | None) -> bool:
    if session is None:
        return False
    return auth_repo.get_user_role(session.user_id) in NEWS_EDITOR_ROLES


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
        article = create_article_via_repository(
            cnt_repo, author_ref_id=current_session.user_id, **body.model_dump()
        )
    except EmptyArticleFieldError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_ARTICLE_FIELD", str(exc))
    except InvalidSlugError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_SLUG", str(exc))
    except SeoFieldTooLongError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "SEO_FIELD_TOO_LONG", str(exc))
    except DuplicateSlugError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_SLUG", str(exc))
    return _to_response(article)


@router.get("", response_model=ArticleListResponse)
def list_published_articles(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category_ref_id: Optional[str] = Query(default=None),
    cnt_repo=Depends(get_cnt_repository),
):
    """CR-017: عام بالكامل — لا جلسة مطلوبة. مُصفَّاة دائمًا إلى status='published'."""
    result = cnt_repo.get_published_articles(page=page, page_size=page_size, category_ref_id=category_ref_id)
    return ArticleListResponse(
        results=[_to_response(a) for a in result.items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=result.total_items),
    )


@router.get("/admin", response_model=ArticleListResponse)
def list_articles_admin(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    """قائمة إدارية تشمل draft/archived — محرر الأخبار فقط."""
    _ensure_news_editor(correlation_id, auth_repo, current_session.user_id)
    result = cnt_repo.list_articles(status=status_filter, page=page, page_size=page_size)
    return ArticleListResponse(
        results=[_to_response(a) for a in result.items],
        pagination=PaginationMeta(page=page, page_size=page_size, total_items=result.total_items),
    )


@router.get("/slug/{slug}", response_model=ArticleResponse)
def get_article_by_slug(
    slug: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session | None = Depends(get_optional_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    article = cnt_repo.get_article_by_slug(slug)
    if article is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", "المقال غير موجود.")
    if article.status != "published" and not _is_news_editor(auth_repo, current_session):
        # 404 لا 401/403 عمدًا: مقال draft/archived لغير المحرر يجب ألا
        # يُفصَح حتى عن وجوده (لا فرق ملحوظ بين "غير موجود" و"موجود لكن
        # غير منشور" لأي طرف لا يملك صلاحية الإدارة).
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", "المقال غير موجود.")
    return _to_response(article)


@router.get("/{article_id}", response_model=ArticleResponse)
def get_article(
    article_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session | None = Depends(get_optional_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    """مقال منشور (published) → عام بالكامل، بلا جلسة (CR-017). غير ذلك
    (draft/archived) → محرر الأخبار حصريًا (نموذج CMS الجديد؛ لا يكفي أي
    جلسة كما كان الحال في السلوك القديم — draft ليس محتوى عامًا لأي مستخدم
    مسجَّل، بل أداة تحرير داخلية). لغير المحرر: 404، لا 401/403 — بلا كشف
    عن وجود المقال أو حالته."""
    article = cnt_repo.get_article_by_id(article_id)
    if article is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", "المقال غير موجود.")
    if article.status != "published" and not _is_news_editor(auth_repo, current_session):
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", "المقال غير موجود.")
    return _to_response(article)


@router.put("/{article_id}", response_model=ArticleResponse)
def update_article(
    article_id: str,
    body: ArticleUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    _ensure_news_editor(correlation_id, auth_repo, current_session.user_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        article = update_article_via_repository(cnt_repo, article_id, **fields)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", str(exc))
    except EmptyArticleFieldError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_ARTICLE_FIELD", str(exc))
    except SeoFieldTooLongError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "SEO_FIELD_TOO_LONG", str(exc))
    return _to_response(article)


@router.put("/{article_id}/slug", response_model=ArticleResponse)
def update_slug(
    article_id: str,
    body: SlugUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    _ensure_news_editor(correlation_id, auth_repo, current_session.user_id)
    try:
        article = change_slug_via_repository(cnt_repo, article_id, body.slug)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", str(exc))
    except InvalidSlugError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_SLUG", str(exc))
    except DuplicateSlugError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_SLUG", str(exc))
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
    except InvalidStatusTransitionError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_TRANSITION", str(exc))
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
    except InvalidStatusTransitionError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_TRANSITION", str(exc))
    return _to_response(article)


@router.post("/{article_id}/archive", response_model=ArticleResponse)
def archive_article(
    article_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cnt_repo=Depends(get_cnt_repository),
    auth_repo=Depends(get_auth_repository_for_editor_check),
):
    _ensure_news_editor(correlation_id, auth_repo, current_session.user_id)
    try:
        article = archive_article_via_repository(cnt_repo, article_id=article_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ARTICLE_NOT_FOUND", str(exc))
    except InvalidStatusTransitionError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_TRANSITION", str(exc))
    return _to_response(article)
