-- ============================================================
-- 016_cr005_phase2_drop_password_column.sql
-- المرحلة (2) من ترحيل CR-005: إزالة عمود password_hash نهائيًا
-- تُنفَّذ فقط بعد التحقق من نجاح المرحلة (1) بصورة كاملة (015)،
-- توصية المالك: مرحلة مستقلة لا تُدمَج مع إنشاء الجداول.
-- ============================================================

-- حارس أمان: يمنع تنفيذ الإزالة إذا وُجد أي مستخدم بكلمة مرور غير مُرحَّلة
DO $$
DECLARE
    unmigrated_count INTEGER;
BEGIN
    SELECT count(*) INTO unmigrated_count
    FROM iam.users u
    WHERE u.password_hash IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM iam.user_identities ui
          WHERE ui.user_id = u.id
            AND ui.provider_type_id = (SELECT id FROM iam.identity_providers WHERE code = 'email_password')
      );

    IF unmigrated_count > 0 THEN
        RAISE EXCEPTION 'يوجد % مستخدم لديهم كلمة مرور غير مُرحَّلة إلى iam.user_identities؛ توقَّف تنفيذ إزالة العمود حتى تُحل هذه الحالة (راجع 015).', unmigrated_count;
    END IF;
END $$;

ALTER TABLE iam.users DROP COLUMN password_hash;

COMMENT ON TABLE iam.users IS 'REQ-IAM-001..009: حساب المستخدم؛ بيانات الاعتماد بالكامل أصبحت في iam.user_identities (CR-005)، لا عمود مباشر هنا';
