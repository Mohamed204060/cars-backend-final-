"""
cnt_service.py — منطق خدمة إدارة المحتوى (CNT) — CMS خفيف
المرجع: REQ-CNT-001، 002 + Master Handoff §8 (Content Management/Articles)

State Machine: draft → published → archived
- draft → published: publish()
- published → draft: unpublish() (رجوع مقصود للتحرير)
- draft → archived: archive()
- published → archived: archive()
- archived: نهائية — لا Transition خارج هذه الوحدة (لا reactivate تلقائي؛
  إن احتاج المحرر إعادة نشر مقال مؤرشف فهذا قرار متعمد يتم عبر مقال جديد
  أو CR يفتح transition إضافي صراحة — لا نخترعه هنا).
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


ARTICLE_STATUSES = {"draft", "published", "archived"}

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9\u0621-\u064A]+")
_SLUG_EDGE_RE = re.compile(r"(^-+|-+$)")

SEO_TITLE_MAX = 70
SEO_DESCRIPTION_MAX = 160


@dataclass
class Article:
    id: str
    author_ref_id: str
    title_ar: str
    body_ar: str
    status: str = "draft"
    title_en: Optional[str] = None
    body_en: Optional[str] = None
    summary_ar: Optional[str] = None
    summary_en: Optional[str] = None
    slug: str = ""
    category_ref_id: Optional[str] = None
    seo_title_ar: Optional[str] = None
    seo_title_en: Optional[str] = None
    seo_description_ar: Optional[str] = None
    seo_description_en: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EmptyArticleFieldError(Exception):
    """العنوان والمحتوى العربيان يجب ألا يكونا فارغين (AR هي اللغة الإلزامية)."""


class InvalidSlugError(Exception):
    """Slug فارغ بعد التطبيع، أو يحتوي أحرفًا غير مسموحة بعد التطهير."""


class DuplicateSlugError(Exception):
    """Slug مستخدَم بالفعل لمقال آخر (UNIQUE constraint على مستوى القاعدة أيضًا)."""


class InvalidStatusTransitionError(Exception):
    """محاولة Transition غير مسموح بها في الـState Machine."""


class SeoFieldTooLongError(Exception):
    """حقل SEO تجاوز الطول المسموح لمحركات البحث (title<=70, description<=160)."""


def slugify(value: str) -> str:
    """يحوّل نصًا عربيًا/إنجليزيًا إلى slug: أحرف/أرقام لاتينية أو عربية
    وشرطات فقط، بلا مسافات أو رموز، ومطبَّع Unicode (NFKC) لتفادي أشكال
    عربية متعددة لنفس الحرف."""
    normalized = unicodedata.normalize("NFKC", value or "").strip().lower()
    slug = _SLUG_STRIP_RE.sub("-", normalized)
    slug = _SLUG_EDGE_RE.sub("", slug)
    return slug


def _validate_seo_field(value: Optional[str], max_len: int, field_name: str) -> None:
    if value and len(value) > max_len:
        raise SeoFieldTooLongError(f"{field_name} يتجاوز الحد المسموح ({max_len} حرفًا).")


def _validate_seo(seo_title_ar, seo_title_en, seo_description_ar, seo_description_en) -> None:
    _validate_seo_field(seo_title_ar, SEO_TITLE_MAX, "seo_title_ar")
    _validate_seo_field(seo_title_en, SEO_TITLE_MAX, "seo_title_en")
    _validate_seo_field(seo_description_ar, SEO_DESCRIPTION_MAX, "seo_description_ar")
    _validate_seo_field(seo_description_en, SEO_DESCRIPTION_MAX, "seo_description_en")


def create_article(
    author_ref_id: str,
    title_ar: str,
    body_ar: str,
    *,
    title_en: Optional[str] = None,
    body_en: Optional[str] = None,
    summary_ar: Optional[str] = None,
    summary_en: Optional[str] = None,
    slug: Optional[str] = None,
    category_ref_id: Optional[str] = None,
    seo_title_ar: Optional[str] = None,
    seo_title_en: Optional[str] = None,
    seo_description_ar: Optional[str] = None,
    seo_description_en: Optional[str] = None,
) -> Article:
    if not title_ar or not title_ar.strip():
        raise EmptyArticleFieldError("عنوان المقال (عربي) يجب ألا يكون فارغًا.")
    if not body_ar or not body_ar.strip():
        raise EmptyArticleFieldError("محتوى المقال (عربي) يجب ألا يكون فارغًا.")

    _validate_seo(seo_title_ar, seo_title_en, seo_description_ar, seo_description_en)

    final_slug = slugify(slug) if slug else slugify(title_ar)
    if not final_slug:
        raise InvalidSlugError("تعذّر توليد slug صالح من العنوان أو القيمة المُدخلة.")

    return Article(
        id="",
        author_ref_id=author_ref_id,
        title_ar=title_ar,
        body_ar=body_ar,
        title_en=title_en,
        body_en=body_en,
        summary_ar=summary_ar,
        summary_en=summary_en,
        slug=final_slug,
        category_ref_id=category_ref_id,
        seo_title_ar=seo_title_ar,
        seo_title_en=seo_title_en,
        seo_description_ar=seo_description_ar,
        seo_description_en=seo_description_en,
        status="draft",
    )


def update_article_fields(article: Article, **fields) -> Article:
    """تحديث حقول قابلة للتعديل فقط (لا id/status/slug عبر هذه الدالة —
    الـslug يُغيَّر عبر مسار صريح منفصل لتفادي كسر روابط خارجية بالخطأ)."""
    _validate_seo(
        fields.get("seo_title_ar", article.seo_title_ar),
        fields.get("seo_title_en", article.seo_title_en),
        fields.get("seo_description_ar", article.seo_description_ar),
        fields.get("seo_description_en", article.seo_description_en),
    )
    for key in (
        "title_ar", "body_ar", "title_en", "body_en", "summary_ar", "summary_en",
        "category_ref_id", "seo_title_ar", "seo_title_en",
        "seo_description_ar", "seo_description_en",
    ):
        if key in fields:
            value = fields[key]
            if key in ("title_ar", "body_ar") and (value is None or not value.strip()):
                raise EmptyArticleFieldError(f"{key} يجب ألا يكون فارغًا.")
            setattr(article, key, value)
    return article


def change_slug(article: Article, new_slug: str) -> Article:
    final_slug = slugify(new_slug)
    if not final_slug:
        raise InvalidSlugError("Slug الجديد فارغ بعد التطبيع.")
    article.slug = final_slug
    return article


def publish_article(article: Article) -> Article:
    if article.status not in ("draft",):
        raise InvalidStatusTransitionError(f"لا يمكن النشر من الحالة الحالية: {article.status}")
    article.status = "published"
    article.published_at = datetime.now(timezone.utc)
    return article


def unpublish_article(article: Article) -> Article:
    if article.status != "published":
        raise InvalidStatusTransitionError(f"لا يمكن إلغاء النشر من الحالة الحالية: {article.status}")
    article.status = "draft"
    return article


def archive_article(article: Article) -> Article:
    if article.status not in ("draft", "published"):
        raise InvalidStatusTransitionError(f"لا يمكن الأرشفة من الحالة الحالية: {article.status}")
    article.status = "archived"
    return article


def create_article_via_repository(repository, author_ref_id: str, **kwargs) -> Article:
    article = create_article(author_ref_id, kwargs.pop("title_ar"), kwargs.pop("body_ar"), **kwargs)
    if repository.get_article_by_slug(article.slug) is not None:
        raise DuplicateSlugError(f"Slug مستخدَم بالفعل: {article.slug}")
    return repository.insert_article(article)


def _get_or_404(repository, article_id: str) -> Article:
    article = repository.get_article_by_id(article_id)
    if article is None:
        raise ValueError(f"لا يوجد مقال بالمعرّف: {article_id}")
    return article


def update_article_via_repository(repository, article_id: str, **fields) -> Article:
    article = _get_or_404(repository, article_id)
    update_article_fields(article, **fields)
    return repository.update_article(article)


def change_slug_via_repository(repository, article_id: str, new_slug: str) -> Article:
    article = _get_or_404(repository, article_id)
    candidate = slugify(new_slug)
    existing = repository.get_article_by_slug(candidate)
    if existing is not None and existing.id != article.id:
        raise DuplicateSlugError(f"Slug مستخدَم بالفعل: {candidate}")
    change_slug(article, new_slug)
    return repository.update_article(article)


def publish_article_via_repository(repository, article_id: str) -> Article:
    article = _get_or_404(repository, article_id)
    publish_article(article)
    return repository.update_article(article)


def unpublish_article_via_repository(repository, article_id: str) -> Article:
    article = _get_or_404(repository, article_id)
    unpublish_article(article)
    return repository.update_article(article)


def archive_article_via_repository(repository, article_id: str) -> Article:
    article = _get_or_404(repository, article_id)
    archive_article(article)
    return repository.update_article(article)
