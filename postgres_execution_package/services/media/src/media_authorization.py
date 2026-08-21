"""
media_authorization.py — Ownership (§7) وView/Signed Access Authorization
(§10) الحقيقيان لكل owner_type — Batch 2 Unit 2
المرجع: CarsMaint Media Foundation — Approved Baseline v1.0

هذا الملف لا يُعرِّف Endpoints ولا يعرف شيئًا عن FastAPI — دوال Builder
خالصة تُنتِج Closures (checkers) تُحقَن في media_api.py عبر app.state، بنفس
نمط is_part_approved_checker القائم في كل المشروع (SSOT، لا استيراد مباشر
لخدمات PR/Offer/Inventory داخل media_api.py نفسها — الاستيراد هنا فقط،
عند التركيب).

Unit 1 استبدلت هذا بـPlaceholder يرفض كل شيء دائمًا (Fail-closed) — Unit 2
تستبدله بالتنفيذ الحقيقي هنا.

⚠️ ملاحظة تركيب حقيقية (CMS Corrective — مهمة لمن يُركِّب التطبيق الفعلي):
هذه الحزمة (postgres_execution_package) لا تحتوي أي ملف تجميع تطبيق فعلي
(main.py/app.py/asgi.py/Dockerfile/إلخ) — تم التحقق بحث شامل في كامل
المستودع المرفَق ولا يوجد أي استدعاء لـFastAPI()/uvicorn.run خارج tests/.
أي تطبيق إنتاجي فعلي (خارج هذا المستودع، غير مرفَق هنا) يستدعي
build_media_ownership_checker يجب أن يمرّر auth_repo=<auth_repository
الفعلي> صراحةً، وإلا فرع "article" أعلاه Fail-closed دائمًا (يرفض كل ربط
صورة بارزة لمقال، حتى لمحرري الأخبار الفعليين) — بصمت افتراضيًا. لتفادي
"ينجح صامتًا بإعداد خاطئ"، الفرع أدناه يُصدِر warnings.warn عند استدعائه
بـauth_repo غائب، فيظهر في أي Log تجميع حقيقي بدل الاختفاء الصامت.
"""

import warnings
from typing import Optional


def build_media_ownership_checker(order_repo, store_repo, inventory_repo, auth_repo=None):
    """
    §7 (Binding Authorization): المستخدم هو uploader (يُتحقَّق منه في
    media_service.create_attachment مسبقًا) + يملك Business Entity
    المستهدف فعليًا. هذه الدالة تتحقق من الشطر الثاني فقط (الملكية).

    article (CMS — Master Handoff §8): لا "ملكية" فردية فعلية في نموذج
    CNT الحالي — أي news_editor يدير أي مقال (نفس _ensure_news_editor في
    cnt_api.py حرفيًا، لا نخترع نموذج ملكية أضيق هنا). auth_repo باراميتر
    اختياري (افتراضي None) للحفاظ على توافق الاستدعاءات القائمة بلا كسر
    (test_media_api.py وtest_postgres_media_api_integration.py يمرِّرانه
    فعليًا الآن في كل الـFixtures الثلاث) — لو غاب وقت الاستدعاء الفعلي
    ولزم فحص article، يُرفَض الربط (Fail-closed) + تحذير صريح (لا صمت).
    """
    def checker(owner_type: str, owner_ref_id: str, uploader_user_ref_id: str) -> bool:
        if owner_type == "purchase_request":
            pr = order_repo.get_purchase_request_by_id(owner_ref_id)
            return pr is not None and pr.buyer_user_ref_id == uploader_user_ref_id

        if owner_type == "offer":
            offer = order_repo.get_offer_by_id(owner_ref_id)
            if offer is None:
                return False
            store = store_repo.get_store_by_id(offer.seller_store_ref_id)
            return store is not None and store.owner_user_ref_id == uploader_user_ref_id

        if owner_type == "inventory_item":
            item = inventory_repo.get_item_by_id(owner_ref_id)
            if item is None:
                return False
            store = store_repo.get_store_by_id(item.store_id)
            return store is not None and store.owner_user_ref_id == uploader_user_ref_id

        if owner_type == "article":
            if auth_repo is None:
                warnings.warn(
                    "build_media_ownership_checker: auth_repo=None عند محاولة ربط "
                    "صورة بارزة لمقال (owner_type='article') — الربط سيُرفَض دائمًا "
                    "(Fail-closed). مرِّر auth_repo الفعلي عند تركيب التطبيق.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return False
            return auth_repo.get_user_role(uploader_user_ref_id) == "news_editor"

        return False

    return checker


def build_public_visibility_checker(cnt_repo=None):
    """
    Corrective (Featured Image public path): (owner_type, owner_ref_id) ->
    bool — يقرِّر هل يجوز كشف مرفق لهذا الـowner للعامة بلا أي جلسة إطلاقًا،
    عبر مسار /media/public/* المستقل تمامًا عن /media/attachments الخاص
    (الذي يبقى بلا أي تعديل — Private PR/Offer كما هي حرفيًا).

    نطاق مقصود وصريح: article فقط حاليًا (لا "كل Media عامة" — inventory_item
    يبقى خارج هذا المسار العام رغم أن OWNER_TYPE_VISIBILITY_POLICY يصنِّفه
    public=True؛ فتحه أيضًا قرار منفصل غير مطلوب هنا، فلا نأخذه ضمنًا).

    article: يتطلب أن يكون المقال منشورًا فعليًا (status='published') عبر
    cnt_repo — لا صورة Draft/Archived تُكشَف للعامة أبدًا حتى لو Attachment
    نفسه status='active'. cnt_repo باراميتر اختياري (نفس نمط auth_repo في
    build_media_ownership_checker) — غيابه يعني Fail-closed + تحذير صريح،
    لا كشف صامت خاطئ.
    """
    def checker(owner_type: str, owner_ref_id: str) -> bool:
        if owner_type == "article":
            if cnt_repo is None:
                warnings.warn(
                    "build_public_visibility_checker: cnt_repo=None عند محاولة كشف "
                    "صورة مقال للعامة (owner_type='article') — سيُرفَض الكشف دائمًا "
                    "(Fail-closed). مرِّر cnt_repo الفعلي عند تركيب التطبيق.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return False
            article = cnt_repo.get_article_by_id(owner_ref_id)
            return article is not None and article.status == "published"

        return False

    return checker


def build_media_view_authorization_checker(order_repo, store_repo):
    """
    §10 (Signed Access) — يخص Private فقط (purchase_request/offer). لا
    يُستدعى لـinventory_item إطلاقًا (Public — بلا حاجة لتفويض عرض، §9).

    PR images: Buyer صاحب الطلب، أو Seller لديه Offer فعلي على الطلب
    نفسه (أي حالة عرض، لا "submitted" فقط — مجرد وجود العرض يكفي وفق
    الـBaseline، لا يُقيَّد بحالة معيَّنة)، أو Admin (يُحسَم خارج هذه
    الدالة عبر is_admin).

    Offer images: Seller صاحب العرض، أو Buyer صاحب PR المرتبط، أو Admin.

    مجرد Login/Seller role غير كافٍ — التحقق هنا دائمًا Ownership حقيقي
    عبر البيانات الفعلية، لا الدور وحده.
    """
    def checker(owner_type: str, owner_ref_id: str, requester_user_ref_id: str, is_admin: bool = False) -> bool:
        if is_admin:
            return True

        if owner_type == "purchase_request":
            pr = order_repo.get_purchase_request_by_id(owner_ref_id)
            if pr is None:
                return False
            if pr.buyer_user_ref_id == requester_user_ref_id:
                return True
            offers, _ = order_repo.list_offers_for_purchase_request_paginated(
                owner_ref_id, status=None, page=1, page_size=1000,
            )
            for offer in offers:
                store = store_repo.get_store_by_id(offer.seller_store_ref_id)
                if store is not None and store.owner_user_ref_id == requester_user_ref_id:
                    return True
            return False

        if owner_type == "offer":
            offer = order_repo.get_offer_by_id(owner_ref_id)
            if offer is None:
                return False
            store = store_repo.get_store_by_id(offer.seller_store_ref_id)
            if store is not None and store.owner_user_ref_id == requester_user_ref_id:
                return True
            pr = order_repo.get_purchase_request_by_id(offer.purchase_request_id)
            return pr is not None and pr.buyer_user_ref_id == requester_user_ref_id

        return False

    return checker
