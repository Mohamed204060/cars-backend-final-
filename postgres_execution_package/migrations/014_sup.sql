-- ============================================================
-- 014_sup.sql — وحدة الدعم الفني (SUP)
-- المرجع: DD الحزمة 1 (قسم SUP)؛ REQ-SUP-001..006
-- الاعتماديات: iam، com — بالإشارة المرجعية
-- ============================================================

CREATE TABLE sup.tickets (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requester_ref_id          UUID NOT NULL,  -- إشارة مرجعية لـ iam.users
    assigned_moderator_ref_id UUID,           -- REQ-SUP-003
    subject                   VARCHAR(256) NOT NULL,
    status                    VARCHAR(16) NOT NULL DEFAULT 'open', -- REQ-SUP-002
    reopen_window_expires_at  TIMESTAMPTZ,     -- REQ-SUP-006
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_tickets_status CHECK (status IN ('open', 'in_progress', 'resolved', 'closed'))
);
COMMENT ON TABLE sup.tickets IS 'REQ-SUP-001..006: طلب الدعم الفني، مع إعادة فتح خلال مهلة';
CREATE INDEX idx_tickets_status ON sup.tickets (status);
CREATE INDEX idx_tickets_requester ON sup.tickets (requester_ref_id);

CREATE TABLE sup.replies (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id     UUID NOT NULL REFERENCES sup.tickets(id),
    author_ref_id UUID NOT NULL,
    body          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE sup.replies IS 'REQ-SUP-005: تبادل ردود متعددة ضمن الطلب نفسه';
CREATE INDEX idx_replies_ticket_id ON sup.replies (ticket_id);
