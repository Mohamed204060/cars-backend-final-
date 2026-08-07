"""
trm_api.py — طبقة REST API لخدمة الثقة والتقييمات (TRM)
المرجع: Final Backend Batch Contract Extension؛ REQ-TRM-001..007

الأهلية (is_eligible_checker): يُحقَن هنا اعتمادًا على OrderRepository فقط
(SSOT، لا استيراد مباشر) — مؤهَّل فقط إن كانت الصفقة المصدر (source_purchase_
request_ref_id) بحالة "fulfilled" وأن رأتَد_by هو فعليًا مشتري تلك الصفقة.
تقييم البائع لتجربة الشراء غير مدعوم بعد (يتطلب ربط seller_store_ref_id
بالمُقيِّم؛ خارج نطاق هذه الدفعة، Backlog).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from session_service import Session
from trm_service import (
    DuplicateRatingError,
    InvalidScoreError,
    InvalidTargetTypeError,
    RatingArchivedImmutableError,
    RatingIneligibleError,
    archive_rating_via_repository,
    create_rating_via_repository,
    get_average_score_via_repository,
    update_rating_via_repository,
)

router = APIRouter(prefix="/api/v1/ratings", tags=["trust"])


class RatingCreateRequest(BaseModel):
    target_type: str
    target_ref_id: str
    source_purchase_request_ref_id: str
    score: int
    comment: Optional[str] = None


class RatingUpdateRequest(BaseModel):
    score: int
    comment: Optional[str] = None


class RatingResponse(BaseModel):
    id: str
    rated_by_user_ref_id: str
    target_type: str
    target_ref_id: str
    source_purchase_request_ref_id: str
    score: int
    comment: Optional[str] = None
    status: str


class AverageScoreResponse(BaseModel):
    target_type: str
    target_ref_id: str
    average_score: Optional[float] = None


def get_trm_repository(request: Request):
    return request.app.state.trm_repository


def get_order_repository_for_eligibility(request: Request):
    return request.app.state.order_repository


def _to_response(rating) -> RatingResponse:
    return RatingResponse(
        id=rating.id, rated_by_user_ref_id=rating.rated_by_user_ref_id, target_type=rating.target_type,
        target_ref_id=rating.target_ref_id, source_purchase_request_ref_id=rating.source_purchase_request_ref_id,
        score=rating.score, comment=rating.comment, status=rating.status,
    )


def _make_eligibility_checker(order_repo, current_user_id: str):
    """REQ-CR-009: مؤهَّل فقط إن أتمّ المستخدم الحالي فعلاً هذه الصفقة كمشترٍ."""
    def _checker(rated_by_user_ref_id: str, target_type: str, source_purchase_request_ref_id: str) -> bool:
        pr = order_repo.get_purchase_request_by_id(source_purchase_request_ref_id)
        if pr is None or pr.status != "fulfilled":
            return False
        return pr.buyer_user_ref_id == rated_by_user_ref_id == current_user_id
    return _checker


@router.post("", response_model=RatingResponse, status_code=status.HTTP_201_CREATED)
def create_rating(
    body: RatingCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    trm_repo=Depends(get_trm_repository),
    order_repo=Depends(get_order_repository_for_eligibility),
):
    checker = _make_eligibility_checker(order_repo, current_session.user_id)
    try:
        rating = create_rating_via_repository(
            trm_repo, rated_by_user_ref_id=current_session.user_id, target_type=body.target_type,
            target_ref_id=body.target_ref_id, source_purchase_request_ref_id=body.source_purchase_request_ref_id,
            score=body.score, is_eligible_checker=checker, comment=body.comment,
        )
    except InvalidTargetTypeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_TARGET_TYPE", str(exc))
    except InvalidScoreError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_SCORE", str(exc))
    except RatingIneligibleError as exc:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "RATING_INELIGIBLE", str(exc))
    except DuplicateRatingError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_RATING", str(exc))
    return _to_response(rating)


@router.get("/target/{target_type}/{target_ref_id}/average", response_model=AverageScoreResponse)
def get_average_score(
    target_type: str,
    target_ref_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    trm_repo=Depends(get_trm_repository),
):
    avg = get_average_score_via_repository(trm_repo, target_type=target_type, target_ref_id=target_ref_id)
    return AverageScoreResponse(target_type=target_type, target_ref_id=target_ref_id, average_score=avg)


@router.patch("/{rating_id}", response_model=RatingResponse)
def update_rating(
    rating_id: str,
    body: RatingUpdateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    trm_repo=Depends(get_trm_repository),
):
    existing = trm_repo.get_rating_by_id(rating_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "RATING_NOT_FOUND", "التقييم غير موجود.")
    if existing.rated_by_user_ref_id != current_session.user_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "لا يجوز تعديل تقييم لا تملكه.")

    try:
        rating = update_rating_via_repository(trm_repo, rating_id=rating_id, new_score=body.score, new_comment=body.comment)
    except InvalidScoreError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_SCORE", str(exc))
    except RatingArchivedImmutableError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "RATING_ARCHIVED", str(exc))
    return _to_response(rating)


@router.post("/{rating_id}/archive", response_model=RatingResponse)
def archive_rating(
    rating_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    trm_repo=Depends(get_trm_repository),
):
    existing = trm_repo.get_rating_by_id(rating_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "RATING_NOT_FOUND", "التقييم غير موجود.")
    if existing.rated_by_user_ref_id != current_session.user_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "لا يجوز أرشفة تقييم لا تملكه.")

    try:
        rating = archive_rating_via_repository(trm_repo, rating_id=rating_id)
    except RatingArchivedImmutableError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "RATING_ARCHIVED", str(exc))
    return _to_response(rating)
