"""
cnt_service.py — منطق خدمة إدارة المحتوى (CNT)
المرجع: REQ-CNT-001، 002
"""

from dataclasses import dataclass


ARTICLE_STATUSES = {"unpublished", "published"}


@dataclass
class Article:
    id: str
    author_ref_id: str
    title: str
    body: str
    status: str = "unpublished"


class EmptyArticleFieldError(Exception):
    """العنوان والمحتوى يجب ألا يكونا فارغين."""


def create_article(author_ref_id: str, title: str, body: str) -> Article:
    if not title or not title.strip():
        raise EmptyArticleFieldError("عنوان المقال يجب ألا يكون فارغًا.")
    if not body or not body.strip():
        raise EmptyArticleFieldError("محتوى المقال يجب ألا يكون فارغًا.")
    return Article(id="", author_ref_id=author_ref_id, title=title, body=body)


def publish_article(article: Article) -> Article:
    """REQ-CNT-002: يجوز النشر من أي حالة (حتى لو منشورة أصلًا — عملية مثالية Idempotent)."""
    article.status = "published"
    return article


def unpublish_article(article: Article) -> Article:
    article.status = "unpublished"
    return article


def create_article_via_repository(repository, author_ref_id: str, title: str, body: str) -> Article:
    article = create_article(author_ref_id, title, body)
    return repository.insert_article(article)


def publish_article_via_repository(repository, article_id: str) -> Article:
    article = repository.get_article_by_id(article_id)
    if article is None:
        raise ValueError(f"لا يوجد مقال بالمعرّف: {article_id}")
    publish_article(article)
    return repository.update_article(article)


def unpublish_article_via_repository(repository, article_id: str) -> Article:
    article = repository.get_article_by_id(article_id)
    if article is None:
        raise ValueError(f"لا يوجد مقال بالمعرّف: {article_id}")
    unpublish_article(article)
    return repository.update_article(article)
