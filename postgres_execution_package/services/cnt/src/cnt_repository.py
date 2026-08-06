"""
cnt_repository.py — طبقة الوصول للبيانات لخدمة إدارة المحتوى (CNT)
المرجع: دليل حوكمة التنفيذ v1.7؛ 013_cnt.sql
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from cnt_service import Article


class CntRepository(ABC):

    @abstractmethod
    def insert_article(self, article: Article) -> Article:
        raise NotImplementedError

    @abstractmethod
    def get_article_by_id(self, article_id: str) -> Optional[Article]:
        raise NotImplementedError

    @abstractmethod
    def get_published_articles(self) -> List[Article]:
        raise NotImplementedError

    @abstractmethod
    def update_article(self, article: Article) -> Article:
        raise NotImplementedError


class PostgresCntRepository(CntRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 013_cnt.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_article(self, article: Article) -> Article:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO cnt.articles (author_ref_id, title, body, status) "
                "VALUES (%(author_ref_id)s, %(title)s, %(body)s, %(status)s) RETURNING id",
                {"author_ref_id": article.author_ref_id, "title": article.title,
                 "body": article.body, "status": article.status},
            )
            article.id = cur.fetchone()["id"]
        return article

    def get_article_by_id(self, article_id: str) -> Optional[Article]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, author_ref_id, title, body, status FROM cnt.articles WHERE id = %(id)s", {"id": article_id}
            )
            row = cur.fetchone()
        return self._row_to_article(row) if row else None

    def get_published_articles(self) -> List[Article]:
        # يعتمد على idx_articles_status
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, author_ref_id, title, body, status FROM cnt.articles WHERE status = 'published'")
            rows = cur.fetchall()
        return [self._row_to_article(r) for r in rows]

    @staticmethod
    def _row_to_article(row) -> Article:
        return Article(id=row["id"], author_ref_id=row["author_ref_id"], title=row["title"],
                        body=row["body"], status=row["status"])

    def update_article(self, article: Article) -> Article:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE cnt.articles SET title = %(title)s, body = %(body)s, status = %(status)s, "
                    "updated_at = now() WHERE id = %(id)s",
                    {"title": article.title, "body": article.body, "status": article.status, "id": article.id},
                )
        return article


class InMemoryCntRepository(CntRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._articles = {}
        self._seq = 1

    def insert_article(self, article: Article) -> Article:
        article.id = f"article-{self._seq}"
        self._seq += 1
        self._articles[article.id] = article
        return article

    def get_article_by_id(self, article_id: str) -> Optional[Article]:
        return self._articles.get(article_id)

    def get_published_articles(self) -> List[Article]:
        return [a for a in self._articles.values() if a.status == "published"]

    def update_article(self, article: Article) -> Article:
        self._articles[article.id] = article
        return article
