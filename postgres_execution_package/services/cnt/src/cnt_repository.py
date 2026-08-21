"""
cnt_repository.py — طبقة الوصول للبيانات لخدمة إدارة المحتوى (CNT)
المرجع: دليل حوكمة التنفيذ v1.7؛ 013_cnt.sql + 034_cnt_articles_cms_seo.sql
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from cnt_service import Article

_COLUMNS = (
    "id, author_ref_id, title_ar, body_ar, status, title_en, body_en, "
    "summary_ar, summary_en, slug, category_ref_id, seo_title_ar, seo_title_en, "
    "seo_description_ar, seo_description_en, "
    "published_at, created_at, updated_at"
)


@dataclass
class ArticlePage:
    items: List[Article]
    total_items: int


class CntRepository(ABC):

    @abstractmethod
    def insert_article(self, article: Article) -> Article:
        raise NotImplementedError

    @abstractmethod
    def get_article_by_id(self, article_id: str) -> Optional[Article]:
        raise NotImplementedError

    @abstractmethod
    def get_article_by_slug(self, slug: str) -> Optional[Article]:
        raise NotImplementedError

    @abstractmethod
    def get_published_articles(self, *, page: int = 1, page_size: int = 20,
                                category_ref_id: Optional[str] = None) -> ArticlePage:
        raise NotImplementedError

    @abstractmethod
    def list_articles(self, *, status: Optional[str] = None, page: int = 1,
                       page_size: int = 20) -> ArticlePage:
        """قائمة إدارية — أي حالة (draft/published/archived)، لمحرري الأخبار فقط."""
        raise NotImplementedError

    @abstractmethod
    def update_article(self, article: Article) -> Article:
        raise NotImplementedError

    @abstractmethod
    def get_latest_published_slugs(self, limit: int = 5000) -> List[str]:
        """للـSitemap فقط: أحدث Slugs منشورة، بلا تحميل الجسم الكامل."""
        raise NotImplementedError


class PostgresCntRepository(CntRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 013_cnt.sql + 034_cnt_articles_cms_seo.sql."""

    def __init__(self, connection):
        self._connection = connection

    def insert_article(self, article: Article) -> Article:
        with self._connection.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO cnt.articles
                    (author_ref_id, title_ar, body_ar, status, title_en, body_en,
                     summary_ar, summary_en, slug, category_ref_id,
                     seo_title_ar, seo_title_en, seo_description_ar, seo_description_en,
                     published_at)
                VALUES
                    (%(author_ref_id)s, %(title_ar)s, %(body_ar)s, %(status)s, %(title_en)s, %(body_en)s,
                     %(summary_ar)s, %(summary_en)s, %(slug)s, %(category_ref_id)s,
                     %(seo_title_ar)s, %(seo_title_en)s, %(seo_description_ar)s, %(seo_description_en)s,
                     %(published_at)s)
                RETURNING id, created_at, updated_at
                """,
                self._to_params(article),
            )
            row = cur.fetchone()
            article.id = row["id"]
            article.created_at = row["created_at"]
            article.updated_at = row["updated_at"]
        return article

    def get_article_by_id(self, article_id: str) -> Optional[Article]:
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM cnt.articles WHERE id = %(id)s", {"id": article_id})
            row = cur.fetchone()
        return self._row_to_article(row) if row else None

    def get_article_by_slug(self, slug: str) -> Optional[Article]:
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM cnt.articles WHERE slug = %(slug)s", {"slug": slug})
            row = cur.fetchone()
        return self._row_to_article(row) if row else None

    def get_published_articles(self, *, page: int = 1, page_size: int = 20,
                                category_ref_id: Optional[str] = None) -> ArticlePage:
        offset = (page - 1) * page_size
        where = "status = 'published'"
        params = {"limit": page_size, "offset": offset}
        if category_ref_id:
            where += " AND category_ref_id = %(category_ref_id)s"
            params["category_ref_id"] = category_ref_id

        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM cnt.articles WHERE {where}", params)
            total = cur.fetchone()["c"]
            cur.execute(
                f"SELECT {_COLUMNS} FROM cnt.articles WHERE {where} "
                f"ORDER BY published_at DESC NULLS LAST LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        return ArticlePage(items=[self._row_to_article(r) for r in rows], total_items=total)

    def list_articles(self, *, status: Optional[str] = None, page: int = 1,
                       page_size: int = 20) -> ArticlePage:
        offset = (page - 1) * page_size
        where = "1=1"
        params = {"limit": page_size, "offset": offset}
        if status:
            where += " AND status = %(status)s"
            params["status"] = status

        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM cnt.articles WHERE {where}", params)
            total = cur.fetchone()["c"]
            cur.execute(
                f"SELECT {_COLUMNS} FROM cnt.articles WHERE {where} "
                f"ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        return ArticlePage(items=[self._row_to_article(r) for r in rows], total_items=total)

    def get_latest_published_slugs(self, limit: int = 5000) -> List[str]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT slug FROM cnt.articles WHERE status = 'published' "
                "ORDER BY published_at DESC NULLS LAST LIMIT %(limit)s",
                {"limit": limit},
            )
            rows = cur.fetchall()
        return [r["slug"] for r in rows]

    def update_article(self, article: Article) -> Article:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cnt.articles SET
                        title_ar = %(title_ar)s, body_ar = %(body_ar)s, status = %(status)s,
                        title_en = %(title_en)s, body_en = %(body_en)s,
                        summary_ar = %(summary_ar)s, summary_en = %(summary_en)s,
                        slug = %(slug)s, category_ref_id = %(category_ref_id)s,
                        seo_title_ar = %(seo_title_ar)s, seo_title_en = %(seo_title_en)s,
                        seo_description_ar = %(seo_description_ar)s, seo_description_en = %(seo_description_en)s,
                        published_at = %(published_at)s, updated_at = now()
                    WHERE id = %(id)s
                    RETURNING updated_at
                    """,
                    self._to_params(article, include_id=True),
                )
                row = cur.fetchone()
                article.updated_at = row["updated_at"]
        return article

    @staticmethod
    def _to_params(article: Article, include_id: bool = False) -> dict:
        params = {
            "author_ref_id": article.author_ref_id, "title_ar": article.title_ar,
            "body_ar": article.body_ar, "status": article.status,
            "title_en": article.title_en, "body_en": article.body_en,
            "summary_ar": article.summary_ar, "summary_en": article.summary_en,
            "slug": article.slug, "category_ref_id": article.category_ref_id,
            "seo_title_ar": article.seo_title_ar, "seo_title_en": article.seo_title_en,
            "seo_description_ar": article.seo_description_ar, "seo_description_en": article.seo_description_en,
            "published_at": article.published_at,
        }
        if include_id:
            params["id"] = article.id
        return params

    @staticmethod
    def _row_to_article(row) -> Article:
        return Article(
            id=row["id"], author_ref_id=row["author_ref_id"], title_ar=row["title_ar"],
            body_ar=row["body_ar"], status=row["status"], title_en=row["title_en"],
            body_en=row["body_en"], summary_ar=row["summary_ar"], summary_en=row["summary_en"],
            slug=row["slug"], category_ref_id=row["category_ref_id"],
            seo_title_ar=row["seo_title_ar"], seo_title_en=row["seo_title_en"],
            seo_description_ar=row["seo_description_ar"], seo_description_en=row["seo_description_en"],
            published_at=row["published_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )


class InMemoryCntRepository(CntRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._articles: dict[str, Article] = {}
        self._seq = 1

    def insert_article(self, article: Article) -> Article:
        article.id = f"article-{self._seq}"
        self._seq += 1
        self._articles[article.id] = article
        return article

    def get_article_by_id(self, article_id: str) -> Optional[Article]:
        return self._articles.get(article_id)

    def get_article_by_slug(self, slug: str) -> Optional[Article]:
        for article in self._articles.values():
            if article.slug == slug:
                return article
        return None

    def get_published_articles(self, *, page: int = 1, page_size: int = 20,
                                category_ref_id: Optional[str] = None) -> ArticlePage:
        items = [a for a in self._articles.values() if a.status == "published"]
        if category_ref_id:
            items = [a for a in items if a.category_ref_id == category_ref_id]
        items.sort(key=lambda a: a.published_at or "", reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return ArticlePage(items=items[start:start + page_size], total_items=total)

    def list_articles(self, *, status: Optional[str] = None, page: int = 1,
                       page_size: int = 20) -> ArticlePage:
        items = list(self._articles.values())
        if status:
            items = [a for a in items if a.status == status]
        total = len(items)
        start = (page - 1) * page_size
        return ArticlePage(items=items[start:start + page_size], total_items=total)

    def get_latest_published_slugs(self, limit: int = 5000) -> List[str]:
        page = self.get_published_articles(page=1, page_size=limit)
        return [a.slug for a in page.items]

    def update_article(self, article: Article) -> Article:
        self._articles[article.id] = article
        return article
