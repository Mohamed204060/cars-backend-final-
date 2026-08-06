-- ============================================================
-- 019_sys_scheduler.sql — المُجدوِل المشترك (Platform Scheduler)
-- المرجع: ADR-035؛ CR-009
-- الاعتماديات: لا شيء (خدمة عامة مستقلة تمامًا عن أي مجال أعمال)
-- الحالة: Prepared — لم يُطبَّق على أي قاعدة بيانات فعلية بعد
-- ============================================================

CREATE TABLE sys.scheduled_jobs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type           VARCHAR(64) NOT NULL,   -- نص حر يحدِّده المستهلِك؛ لا معرفة بمجال بعينه هنا
    target_ref_id      UUID NOT NULL,          -- إشارة مرجعية فقط لكيان المستهلِك
    scheduled_at       TIMESTAMPTZ NOT NULL,
    recurrence_rule    VARCHAR(16),            -- NULL | daily | weekly | monthly
    status             VARCHAR(16) NOT NULL DEFAULT 'pending',
    last_run_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_scheduled_jobs_status CHECK (status IN ('pending', 'executing', 'completed', 'cancelled', 'failed')),
    CONSTRAINT chk_scheduled_jobs_recurrence CHECK (recurrence_rule IS NULL OR recurrence_rule IN ('daily', 'weekly', 'monthly'))
);
COMMENT ON TABLE sys.scheduled_jobs IS 'ADR-035: مُجدوِل عام قابل لإعادة الاستخدام من أي مجال (PUR، NTF، وأي مجال مستقبلي)؛ لا حذف فعلي — الإزالة عبر status=cancelled فقط';

-- الفهرس المركَّب الأهم: يخدم استعلام get_pending_jobs_due_before المستخدَم من كل المستهلِكين دوريًا
CREATE INDEX idx_scheduled_jobs_status_scheduled_at ON sys.scheduled_jobs (status, scheduled_at)
    WHERE status = 'pending';
CREATE INDEX idx_scheduled_jobs_job_type ON sys.scheduled_jobs (job_type);
