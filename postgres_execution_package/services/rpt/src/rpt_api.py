"""
rpt_api.py — طبقة REST API لتقارير الإدارة (Batch 3A Slice 2)

GET /reports/executive-dashboard: للإداري (SYSTEM_ADMIN_ROLES) حصرًا — نفس
نمط aud_api.py/ana_api.py حرفيًا. لا Endpoint للكتابة إطلاقًا (Read-Only Domain).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from rpt_service import InvalidDateRangeError, get_executive_dashboard_via_repository

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class ExecutiveDashboardResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    users_total: int
    users_new: int
    users_by_status: dict[str, int]
    sellers_total: int

    stores_total: int
    stores_by_status: dict[str, int]

    inventory_items_total: int
    inventory_items_by_status: dict[str, int]

    catalog_parts_total: int
    catalog_parts_by_status: dict[str, int]

    purchase_requests_total: int
    purchase_requests_by_status: dict[str, int]
    purchase_requests_without_offers: int

    offers_total: int
    offers_by_status: dict[str, int]

    request_to_offer_rate: float
    request_to_accepted_offer_rate: float
    avg_offers_per_request: float

    subscriptions_active_total: int
    subscriptions_by_plan: dict[str, int]


def get_rpt_repository(request: Request):
    return request.app.state.rpt_repository


def _to_response(d) -> ExecutiveDashboardResponse:
    return ExecutiveDashboardResponse(
        generated_at_utc=d.generated_at_utc.isoformat(),
        date_from=d.date_from.isoformat() if d.date_from else None,
        date_to=d.date_to.isoformat() if d.date_to else None,
        users_total=d.users_total, users_new=d.users_new, users_by_status=d.users_by_status,
        sellers_total=d.sellers_total,
        stores_total=d.stores_total, stores_by_status=d.stores_by_status,
        inventory_items_total=d.inventory_items_total, inventory_items_by_status=d.inventory_items_by_status,
        catalog_parts_total=d.catalog_parts_total, catalog_parts_by_status=d.catalog_parts_by_status,
        purchase_requests_total=d.purchase_requests_total, purchase_requests_by_status=d.purchase_requests_by_status,
        purchase_requests_without_offers=d.purchase_requests_without_offers,
        offers_total=d.offers_total, offers_by_status=d.offers_by_status,
        request_to_offer_rate=d.request_to_offer_rate,
        request_to_accepted_offer_rate=d.request_to_accepted_offer_rate,
        avg_offers_per_request=d.avg_offers_per_request,
        subscriptions_active_total=d.subscriptions_active_total, subscriptions_by_plan=d.subscriptions_by_plan,
    )


@router.get("/executive-dashboard", response_model=ExecutiveDashboardResponse)
def get_executive_dashboard(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")

    try:
        dashboard = get_executive_dashboard_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return _to_response(dashboard)
