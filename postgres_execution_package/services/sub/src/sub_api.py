"""
sub_api.py — طبقة REST API لخدمة الاشتراكات (SUB)
المرجع: Final Backend Batch (SUB/CNT/SUP) Contract Extension؛ REQ-SUB-001..005

REQ-SUB-001: مدير النظام حصريًا لتعريف الخطط. REQ-SUB-002/005: البائع نفسه
لاشتراكه/تغيير خطته (لا فحص دور، بل مطابقة seller_ref_id بالجلسة).

CR-014: عضوية Free دائمة لكل بائع — GET /mine يُنشئ اشتراك Free تلقائيًا
لبائع لم يشترك قط بدل إعادة null؛ التوقيع (Optional) أُبقي دفاعيًا فقط.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from sub_service import (
    InvalidDurationError,
    SellerAlreadySubscribedError,
    change_plan_via_repository,
    create_plan_via_repository,
    get_my_subscription_via_repository,
    subscribe_seller_via_repository,
)

router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])


class PlanCreateRequest(BaseModel):
    plan_type_ref_id: str


class PlanResponse(BaseModel):
    id: str
    plan_type_ref_id: str
    is_free: bool = False


class SubscribeRequest(BaseModel):
    plan_id: str
    duration_days: int = 30


class ChangePlanRequest(BaseModel):
    new_plan_id: str


class SubscriptionResponse(BaseModel):
    id: str
    seller_ref_id: str
    plan_id: str
    status: str
    expires_at: Optional[datetime] = None


def get_sub_repository(request: Request):
    return request.app.state.sub_repository


def _to_plan_response(plan) -> PlanResponse:
    return PlanResponse(id=plan.id, plan_type_ref_id=plan.plan_type_ref_id, is_free=plan.is_free)


def _to_sub_response(sub) -> SubscriptionResponse:
    return SubscriptionResponse(id=sub.id, seller_ref_id=sub.seller_ref_id, plan_id=sub.plan_id,
                                 status=sub.status, expires_at=sub.expires_at)


@router.post("/plans", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
def create_plan(
    body: PlanCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sub_repo=Depends(get_sub_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "تعريف خطط الاشتراك مقصور على مدير النظام.")
    try:
        plan = create_plan_via_repository(sub_repo, plan_type_ref_id=body.plan_type_ref_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_PLAN_TYPE", str(exc))
    return _to_plan_response(plan)


@router.get("/plans", response_model=list[PlanResponse])
def list_plans(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sub_repo=Depends(get_sub_repository),
):
    return [_to_plan_response(p) for p in sub_repo.get_all_plans()]


@router.post("", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
def subscribe(
    body: SubscribeRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sub_repo=Depends(get_sub_repository),
):
    try:
        sub = subscribe_seller_via_repository(
            sub_repo, seller_ref_id=current_session.user_id, plan_id=body.plan_id,
            now=datetime.now(timezone.utc), duration_days=body.duration_days,
        )
    except InvalidDurationError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DURATION", str(exc))
    except SellerAlreadySubscribedError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "ALREADY_SUBSCRIBED", str(exc))
    return _to_sub_response(sub)


@router.get("/mine", response_model=Optional[SubscriptionResponse])
def get_my_subscription(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sub_repo=Depends(get_sub_repository),
):
    sub = get_my_subscription_via_repository(sub_repo, seller_ref_id=current_session.user_id, now=datetime.now(timezone.utc))
    return _to_sub_response(sub) if sub is not None else None


@router.post("/{subscription_id}/change-plan", response_model=SubscriptionResponse)
def change_plan(
    subscription_id: str,
    body: ChangePlanRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    sub_repo=Depends(get_sub_repository),
):
    existing = sub_repo.get_subscription_by_id(subscription_id)
    if existing is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "SUBSCRIPTION_NOT_FOUND", "الاشتراك غير موجود.")
    if existing.seller_ref_id != current_session.user_id:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "لا يجوز تعديل اشتراك لا يخصك.")

    try:
        sub = change_plan_via_repository(sub_repo, subscription_id=subscription_id, new_plan_id=body.new_plan_id,
                                          now=datetime.now(timezone.utc))
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "SUBSCRIPTION_NOT_ACTIVE", str(exc))
    return _to_sub_response(sub)
