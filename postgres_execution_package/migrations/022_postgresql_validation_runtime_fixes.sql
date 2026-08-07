-- 022_postgresql_validation_runtime_fixes.sql
-- Governance: corrective migration created after live PostgreSQL validation.
-- It intentionally does NOT rewrite immutable migrations 009_str.sql or 018_ntf.sql.
-- Scope:
--   1) allow the approved pricing mode value "contact_for_price";
--   2) enforce BR-NTF-006 append-only behavior for template versions at DB level.

BEGIN;

ALTER TABLE str.inventory_items
    ALTER COLUMN pricing_mode TYPE VARCHAR(32);

CREATE OR REPLACE FUNCTION ntf.reject_template_version_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'ntf.template_versions is append-only; UPDATE and DELETE are forbidden'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

DROP TRIGGER IF EXISTS trg_template_versions_append_only ON ntf.template_versions;
CREATE TRIGGER trg_template_versions_append_only
BEFORE UPDATE OR DELETE ON ntf.template_versions
FOR EACH ROW
EXECUTE FUNCTION ntf.reject_template_version_mutation();

COMMIT;
