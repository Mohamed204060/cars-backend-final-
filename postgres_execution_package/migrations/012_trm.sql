-- ============================================================
-- 012_trm.sql — وحدة الثقة والإشراف (TRM)
-- المرجع: DD الحزمة 1 (قسم TRM)؛ REQ-TRM-001..008
-- الاعتماديات: iam، pur، str — بالإشارة المرجعية
-- ============================================================

CREATE TABLE trm.ratings (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rated_seller_ref_id    UUID NOT NULL,   -- إشارة مرجعية لـ iam.users
    rater_buyer_ref_id     UUID NOT NULL,   -- إشارة مرجعية لـ iam.users
    score                  SMALLINT NOT NULL,           -- REQ-TRM-001: درجة رقمية إلزامية
    comment_text           TEXT,                        -- تعليق نصي اختياري
    edit_window_expires_at TIMESTAMPTZ NOT NULL,        -- REQ-TRM-002
    is_removed_by_moderator BOOLEAN NOT NULL DEFAULT false,  -- REQ-TRM-004: حذف منطقي إشرافي
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_ratings_score CHECK (score BETWEEN 1 AND 5)
);
COMMENT ON TABLE trm.ratings IS 'REQ-TRM-001..008: تقييم المشتري للبائع فقط في هذا الإصدار (اتجاه واحد)';
CREATE INDEX idx_ratings_seller ON trm.ratings (rated_seller_ref_id) WHERE is_removed_by_moderator = false;

CREATE TABLE trm.reports (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type   VARCHAR(24) NOT NULL,  -- متجر/مستخدم/رسالة/عنصر مخزون REQ-TRM-005
    target_ref_id UUID NOT NULL,
    reporter_ref_id UUID NOT NULL,       -- إشارة مرجعية لـ iam.users
    status        VARCHAR(16) NOT NULL DEFAULT 'under_review', -- REQ-TRM-006
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_reports_target_type CHECK (target_type IN ('store', 'user', 'message', 'inventory_item')),
    CONSTRAINT chk_reports_status CHECK (status IN ('under_review', 'accepted', 'rejected'))
);
COMMENT ON TABLE trm.reports IS 'REQ-TRM-005, 006: البلاغات، تشمل عناصر المخزون';
CREATE INDEX idx_reports_target ON trm.reports (target_type, target_ref_id);
CREATE INDEX idx_reports_status ON trm.reports (status);

CREATE TABLE trm.disputes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_ref_id  UUID NOT NULL,
    seller_ref_id UUID NOT NULL,
    status      VARCHAR(16) NOT NULL DEFAULT 'open',  -- REQ-TRM-007: توثيقية غير ملزمة
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_disputes_status CHECK (status IN ('open', 'closed'))
);
COMMENT ON TABLE trm.disputes IS 'REQ-TRM-007: أداة توثيق نزاع تنظيمية، غير ملزمة';
