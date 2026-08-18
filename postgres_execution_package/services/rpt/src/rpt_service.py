"""
rpt_service.py — طبقة التنسيق لتقارير الإدارة (Batch 3A Slice 2)

===========================================================================
Reporting Data Dictionary — Executive Dashboard + Marketplace Conversion
===========================================================================
كل Metric هنا مُعرَّف مرة واحدة فقط (SSOT) — أي استهلاك مستقبلي له (Export،
Regulatory Report، إلخ) يجب أن يستدعي rpt_repository.get_executive_dashboard
نفسها، لا إعادة حساب الصيغة في مكان آخر.

--- users_total ---
Formula: COUNT(*) FROM iam.users
Included states: كل الحالات (active/suspended/banned/archived)
Date semantics: Snapshot لحظة الاستعلام
Filters: لا شيء

--- users_new ---
Formula: COUNT(*) FROM iam.users WHERE created_at BETWEEN date_from AND date_to
Included states: كل الحالات
Date semantics: يتطلب date_from و/أو date_to صراحة؛ 0 بلا مدى (لا معنى لـ"جدد" بلا نافذة زمنية)
Null handling: date_from/date_to اختياريان كل على حدة (>= أو <= فقط إن وُجد أحدهما)

--- users_by_status / sellers_total ---
Formula: GROUP BY status | primary_role IN ('individual_seller','business_seller')
ملاحظة: sellers_total يُحسَب من iam.users.primary_role (الدور)، وليس من عدد
المتاجر (بائع واحد قد يملك متجرًا واحدًا كحد أقصى وفق REQ-STR الحالية، لكن
هذا Metric يقيس المستخدمين ذوي دور بائع بصرف النظر عن امتلاكهم متجرًا فعليًا).

--- stores_total / stores_by_status ---
Formula: GROUP BY status FROM str.stores (creating/active/suspended/archived)
Date semantics: Snapshot

--- inventory_items_total / inventory_items_by_status ---
Formula: GROUP BY status FROM str.inventory_items (active/out_of_stock/hidden/archived)
Date semantics: Snapshot

--- catalog_parts_total / catalog_parts_by_status ---
Formula: GROUP BY status FROM pct.catalog_parts (proposed/approved/archived)
Date semantics: Snapshot

--- purchase_requests_total / purchase_requests_by_status ---
Formula: GROUP BY status FROM pur.purchase_requests (open/under_review/fulfilled/expired/cancelled)
Date semantics: Snapshot

--- purchase_requests_without_offers ---
Formula: COUNT(pr) WHERE NOT EXISTS (SELECT 1 FROM pur.offers WHERE purchase_request_id = pr.id)
Included states: كل حالات الطلب (بغضّ النظر عن status، فقط غياب أي عرض)

--- offers_total / offers_by_status ---
Formula: GROUP BY status FROM pur.offers (submitted/accepted/rejected/withdrawn/expired)

--- request_to_offer_rate ---
Formula: (purchase_requests_total - purchase_requests_without_offers) / purchase_requests_total
Null handling: 0.0 إذا purchase_requests_total = 0 (Division-by-zero محروسة)
Sensitivity: Marketplace Matching & Conversion — ليس Financial Metric (§7 من الكتالوج)

--- request_to_accepted_offer_rate ---
Formula: purchase_requests_by_status['fulfilled'] / purchase_requests_total
Null handling: 0.0 إذا purchase_requests_total = 0
ملاحظة حاكمة صريحة: Accepted Offer ≠ Completed Financial Sale — هذا Metric
يقيس معدّل التوفيق (Matching) داخل السوق فقط، لا مبيعات مالية فعلية (لا
Payment Gateway في V1).

--- avg_offers_per_request ---
Formula: offers_total / purchase_requests_total
Null handling: 0.0 إذا purchase_requests_total = 0

--- subscriptions_active_total / subscriptions_by_plan ---
Formula: GROUP BY ref.ref_values.code FROM sub.seller_subscriptions WHERE status='active'
         JOIN sub.plans JOIN ref.ref_values
Date semantics: Snapshot (الاشتراكات النشطة الآن، لا يُفلتَر بالمدى الزمني)

===========================================================================
كل المؤشرات أعلاه: Sensitivity = Internal (لا بيانات شخصية حساسة، أرقام
مجمَّعة فقط — لا IP/Login/Messaging هنا؛ تلك في نطاق AUD منفصل تمامًا).
لا Revenue/Profit/GMV/Commission/Refunds — V1 ليس طرفًا ماليًا (قرار حاكم صريح).
===========================================================================
"""

from datetime import datetime
from typing import Optional

from rpt_repository import ExecutiveDashboard


class InvalidDateRangeError(ValueError):
    pass


def get_executive_dashboard_via_repository(
    repository, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
) -> ExecutiveDashboard:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidDateRangeError("date_from يجب ألا يكون بعد date_to.")
    return repository.get_executive_dashboard(date_from, date_to)
