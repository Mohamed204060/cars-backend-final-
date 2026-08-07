"""
trm_repository.py — طبقة الوصول للبيانات لخدمة الثقة والتقييمات (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.7

قيد قاعدة البيانات UNIQUE(rated_by_user_ref_id, target_type, target_ref_id,
source_purchase_request_ref_id) هو الضامن الفعلي لمنع التكرار على مستوى
المحرك نفسه، لا منطق التطبيق فقط. لا دالة حذف فعلي عمدًا.
"""

from abc import ABC, abstractmethod
from typing import Optional, List

from trm_service import Rating, DuplicateRatingError


class TrmRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه trm_service.py. لا دالة حذف عمدًا."""

    @abstractmethod
    def insert_rating(self, rating: Rating) -> Rating: raise NotImplementedError

    @abstractmethod
    def get_rating_by_id(self, rating_id: str) -> Optional[Rating]: raise NotImplementedError

    @abstractmethod
    def update_rating(self, rating: Rating) -> Rating: raise NotImplementedError

    @abstractmethod
    def get_ratings_for_source_purchase_request(self, source_purchase_request_ref_id: str) -> List[Rating]:
        raise NotImplementedError

    @abstractmethod
    def get_ratings_for_target(self, target_type: str, target_ref_id: str) -> List[Rating]:
        raise NotImplementedError


class PostgresTrmRepository(TrmRepository):
    """تنفيذ فعلي عبر PostgreSQL. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_rating(self, rating: Rating) -> Rating:
        # يعتمد على uq_ratings_rater_target_source (فهرس تفرّد يضمن Deduplication فعليًا)
        try:
            with self._connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO trm.ratings (rated_by_user_ref_id, target_type, target_ref_id, "
                    "source_purchase_request_ref_id, score, comment, status) "
                    "VALUES (%(r)s, %(tt)s, %(tr)s, %(sr)s, %(sc)s, %(c)s, %(s)s) RETURNING id, created_at",
                    {"r": rating.rated_by_user_ref_id, "tt": rating.target_type, "tr": rating.target_ref_id,
                     "sr": rating.source_purchase_request_ref_id, "sc": rating.score,
                     "c": rating.comment, "s": rating.status},
                )
                row = cur.fetchone()
                rating.id = row["id"]
                rating.created_at = row["created_at"]
        except Exception as exc:
            if "unique" in str(exc).lower():
                raise DuplicateRatingError(
                    "يوجد بالفعل تقييم لهذا الهدف من هذا المستخدم عن هذه الصفقة (تعارض تزامن)."
                ) from exc
            raise
        return rating

    def get_rating_by_id(self, rating_id: str) -> Optional[Rating]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM trm.ratings WHERE id = %(id)s", {"id": rating_id})
            row = cur.fetchone()
        return self._row_to_rating(row) if row else None

    def update_rating(self, rating: Rating) -> Rating:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE trm.ratings SET score = %(sc)s, comment = %(c)s, status = %(s)s WHERE id = %(id)s",
                    {"sc": rating.score, "c": rating.comment, "s": rating.status, "id": rating.id},
                )
        return rating

    def get_ratings_for_source_purchase_request(self, source_purchase_request_ref_id: str) -> List[Rating]:
        # يعتمد على idx_ratings_source_purchase_request
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM trm.ratings WHERE source_purchase_request_ref_id = %(pr)s",
                {"pr": source_purchase_request_ref_id},
            )
            rows = cur.fetchall()
        return [self._row_to_rating(r) for r in rows]

    def get_ratings_for_target(self, target_type: str, target_ref_id: str) -> List[Rating]:
        # يعتمد على idx_ratings_target (مركَّب target_type + target_ref_id)
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM trm.ratings WHERE target_type = %(tt)s AND target_ref_id = %(tr)s",
                {"tt": target_type, "tr": target_ref_id},
            )
            rows = cur.fetchall()
        return [self._row_to_rating(r) for r in rows]

    @staticmethod
    def _row_to_rating(row) -> Rating:
        return Rating(id=row["id"], rated_by_user_ref_id=row["rated_by_user_ref_id"],
                       target_type=row["target_type"], target_ref_id=row["target_ref_id"],
                       source_purchase_request_ref_id=row["source_purchase_request_ref_id"],
                       score=row["score"], comment=row["comment"], status=row["status"],
                       created_at=row["created_at"])


class InMemoryTrmRepository(TrmRepository):
    """تنفيذ وهمي للاختبار فقط؛ يحاكي قيد التفرّد فعليًا. لا دالة حذف هنا، عمدًا."""

    def __init__(self):
        self._ratings = {}
        self._next_seq = 1

    def insert_rating(self, rating: Rating) -> Rating:
        for existing in self._ratings.values():
            if (existing.rated_by_user_ref_id == rating.rated_by_user_ref_id
                    and existing.target_type == rating.target_type
                    and existing.target_ref_id == rating.target_ref_id
                    and existing.source_purchase_request_ref_id == rating.source_purchase_request_ref_id):
                raise DuplicateRatingError(
                    "يوجد بالفعل تقييم لهذا الهدف من هذا المستخدم عن هذه الصفقة (تعارض تزامن)."
                )
        rating.id = f"rating-{self._next_seq}"
        self._next_seq += 1
        self._ratings[rating.id] = rating
        return rating

    def get_rating_by_id(self, rating_id: str) -> Optional[Rating]:
        return self._ratings.get(rating_id)

    def update_rating(self, rating: Rating) -> Rating:
        self._ratings[rating.id] = rating
        return rating

    def get_ratings_for_source_purchase_request(self, source_purchase_request_ref_id: str) -> List[Rating]:
        return [r for r in self._ratings.values()
                if r.source_purchase_request_ref_id == source_purchase_request_ref_id]

    def get_ratings_for_target(self, target_type: str, target_ref_id: str) -> List[Rating]:
        return [r for r in self._ratings.values()
                if r.target_type == target_type and r.target_ref_id == target_ref_id]
