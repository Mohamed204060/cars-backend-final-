-- ============================================================
-- 013_cnt.sql — وحدة إدارة المحتوى (CNT)
-- المرجع: DD الحزمة 1 (قسم CNT)؛ REQ-CNT-001، 002
-- الاعتماديات: iam — بالإشارة المرجعية
-- ============================================================

CREATE TABLE cnt.articles (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    author_ref_id UUID NOT NULL,   -- إشارة مرجعية لمحرر أخبار في iam
    title         VARCHAR(256) NOT NULL,
    body          TEXT NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'unpublished',  -- REQ-CNT-002
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_articles_status CHECK (status IN ('unpublished', 'published'))
);
COMMENT ON TABLE cnt.articles IS 'REQ-CNT-001, 002: المقالة/الخبر';
CREATE INDEX idx_articles_status ON cnt.articles (status);
