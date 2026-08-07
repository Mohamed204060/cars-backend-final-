-- ============================================================
-- 009_str.sql — وحدة المتاجر ومخزون البائعين (STR)
-- المرجع: DD الحزمة 1 (قسم STR)؛ REQ-STR-001..025 (يشمل سياسة الصور والتسعير، Change Request CR-001 لاستبعاد الفروع)
-- الاعتماديات: pct، ref، iam، sub — بالإشارة المرجعية
-- ============================================================

CREATE TABLE str.stores (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_ref_id UUID NOT NULL,  -- إشارة مرجعية لـ iam.users؛ لا نقل ملكية من البائع (REQ-STR-005)
    status            VARCHAR(16) NOT NULL DEFAULT 'creating', -- REQ-STR-004
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_stores_status CHECK (status IN ('creating', 'active', 'suspended', 'archived'))
);
COMMENT ON TABLE str.stores IS 'REQ-STR-001..008: المتجر؛ لا فروع في الإصدار الأول (CR-001)';
CREATE INDEX idx_stores_owner ON str.stores (owner_user_ref_id);
CREATE INDEX idx_stores_status ON str.stores (status);

CREATE TABLE str.inventory_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_code       VARCHAR(32) NOT NULL,          -- REQ-STR-018
    store_id            UUID NOT NULL REFERENCES str.stores(id),
    catalog_part_ref_id UUID NOT NULL,                 -- إشارة مرجعية لـ pct.catalog_parts (REQ-STR-009)
    condition_ref_id    UUID NOT NULL,                 -- إشارة مرجعية لـ ref.ref_values (part_condition)
    pricing_mode        VARCHAR(16) NOT NULL,          -- REQ-STR-012
    price_amount        NUMERIC(12,2),
    price_currency      CHAR(3),
    quantity            INTEGER NOT NULL DEFAULT 0,
    status              VARCHAR(16) NOT NULL DEFAULT 'active', -- REQ-STR-017
    primary_photo_id    UUID,                          -- يُربَط لاحقًا بـ inventory_photos.id (REQ-STR-024-B)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_inventory_items_business_code UNIQUE (business_code),
    CONSTRAINT chk_inventory_items_pricing_mode CHECK (pricing_mode IN ('fixed_price', 'contact_for_price')),
    CONSTRAINT chk_inventory_items_status CHECK (status IN ('active', 'out_of_stock', 'hidden', 'archived')),
    CONSTRAINT chk_inventory_items_quantity CHECK (quantity >= 0),
    -- REQ-STR-012: السعر إلزامي فقط عند اختيار "سعر محدد"
    CONSTRAINT chk_inventory_items_price_mode CHECK (
        (pricing_mode = 'fixed_price' AND price_amount IS NOT NULL AND price_currency IS NOT NULL)
        OR (pricing_mode = 'contact_for_price' AND price_amount IS NULL)
    )
);
COMMENT ON TABLE str.inventory_items IS 'REQ-STR-009..019: عنصر مخزون البائع، بالإشارة لقطعة الكتالوج لا بنسخها';
CREATE INDEX idx_inventory_items_store_id ON str.inventory_items (store_id);
CREATE INDEX idx_inventory_items_part ON str.inventory_items (catalog_part_ref_id);
CREATE INDEX idx_inventory_items_status ON str.inventory_items (status);

CREATE TABLE str.inventory_photos (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inventory_item_id   UUID NOT NULL REFERENCES str.inventory_items(id),
    original_asset_ref  TEXT NOT NULL,   -- REQ-STR-024: الصورة الأصلية دون تعديل
    display_asset_ref   TEXT NOT NULL,   -- REQ-STR-023، 024-A: نسخة العرض بعلامة مائية ومعالجة
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE str.inventory_photos IS 'REQ-STR-020..024-B: صور عنصر المخزون (أصلية + نسخة عرض)';
CREATE INDEX idx_inventory_photos_item_id ON str.inventory_photos (inventory_item_id);

ALTER TABLE str.inventory_items
    ADD CONSTRAINT fk_inventory_items_primary_photo
    FOREIGN KEY (primary_photo_id) REFERENCES str.inventory_photos(id);
