-- 023_iam_sessions.sql
-- Governance: CR-013 — REQ-SEC-004 (انتهاء الجلسة تلقائيًا عند عدم النشاط)،
-- REQ-SEC-005 (إلغاء الجلسة فورًا عند تسجيل الخروج أو الحظر).
-- لا تعديل على أي Migration تاريخية (000-022)؛ ملف جديد ومستقل بالكامل،
-- بنفس نمط Migration 022 (BEGIN/COMMIT ذرّي، إمكانية إعادة تشغيل آمنة).

BEGIN;

CREATE TABLE iam.sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES iam.users(id),
    -- REQ-SEC-002 امتدادًا: يُخزَّن بصمة SHA-256 للتوكن فقط، لا التوكن الخام إطلاقًا.
    token_hash      VARCHAR(64) NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    -- REQ-SEC-004: يُحدَّث عند كل استخدام فعلي للجلسة (Sliding Window)؛
    -- expires_at يُعاد حسابه من last_active_at + مهلة الخمول القابلة للضبط.
    last_active_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    -- REQ-SEC-005 + REQ-AUD-005: يُسجَّل وقت وسبب الإنهاء عند الإبطال الفوري.
    revoked_at      TIMESTAMP WITH TIME ZONE,
    revoked_reason  VARCHAR(32),
    CONSTRAINT uq_sessions_token_hash UNIQUE (token_hash),
    CONSTRAINT chk_sessions_revoked_reason CHECK (
        revoked_reason IS NULL
        OR revoked_reason IN ('logout', 'idle_timeout', 'admin_ban', 'admin_revoke')
    ),
    CONSTRAINT chk_sessions_revoked_consistency CHECK (
        (revoked_at IS NULL AND revoked_reason IS NULL)
        OR (revoked_at IS NOT NULL AND revoked_reason IS NOT NULL)
    )
);

COMMENT ON TABLE iam.sessions IS
    'REQ-SEC-004/005، REQ-AUD-005 (CR-013): جلسات المستخدم بعد تسجيل الدخول. '
    'يُخزَّن بصمة التوكن (SHA-256 hex، 64 حرفًا) لا التوكن الخام، اتساقًا مع REQ-SEC-002.';

-- بحث سريع عن كل جلسات مستخدم معيّن (لإبطال جماعي عند الحظر، REQ-SEC-005)
CREATE INDEX idx_sessions_user_id ON iam.sessions (user_id);

-- فهرس جزئي: يخدم مسار التحقق الأكثر تكرارًا (جلسة نشطة غير مُبطَلة) فقط
CREATE INDEX idx_sessions_active_lookup ON iam.sessions (token_hash) WHERE revoked_at IS NULL;

COMMIT;
