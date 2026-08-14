"""
media_repository.py — طبقة تخزين Media Foundation
المرجع: CarsMaint Media Foundation — Approved Baseline v1.0
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from media_service import Asset, Attachment
from advisory_lock import compute_advisory_lock_key


class MediaRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه media_api.py."""

    # --- media.assets ---

    @abstractmethod
    def insert_asset(self, asset: Asset) -> Asset:
        raise NotImplementedError

    @abstractmethod
    def get_asset_by_id(self, asset_id: str) -> Optional[Asset]:
        raise NotImplementedError

    @abstractmethod
    def update_asset_processing_result(
        self, asset_id: str, status: str,
        storage_key: Optional[str] = None, storage_key_display: Optional[str] = None,
        storage_key_thumbnail: Optional[str] = None, mime_type: Optional[str] = None,
        size_bytes: Optional[int] = None, checksum: Optional[str] = None,
        width: Optional[int] = None, height: Optional[int] = None,
    ) -> Asset:
        """§3-4: يُستدعى بعد اكتمال (أو فشل) المعالجة — ينقل status ويملأ حقول النتيجة دفعة واحدة."""
        raise NotImplementedError

    @abstractmethod
    def archive_asset(self, asset_id: str) -> Asset:
        raise NotImplementedError

    @abstractmethod
    def list_unbound_ready_assets_older_than(self, cutoff_timestamp) -> List[Asset]:
        """§11: Assets بحالة ready بلا أي Attachment، أقدم من المهلة — لتنظيف Orphans."""
        raise NotImplementedError

    # --- media.attachments ---

    @abstractmethod
    def get_active_attachment_count_for_owner(self, owner_type: str, owner_ref_id: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def insert_attachment_with_lock(
        self, asset: Asset, owner_type: str, owner_ref_id: str,
        is_uploader_owner_checker,
    ) -> Attachment:
        """
        §6/§15: COUNT + sort_order + INSERT تحت Advisory Transaction Lock
        واحد، namespace='media-binding'. يفوِّض التحقق (Authorization +
        Limit) لـmedia_service.create_attachment بعد حساب count الفعلي
        تحت القفل — لا افتراض عدد من خارج القفل.
        """
        raise NotImplementedError

    @abstractmethod
    def list_attachments_for_owner(self, owner_type: str, owner_ref_id: str, status: str = "active") -> List[Attachment]:
        raise NotImplementedError

    @abstractmethod
    def get_attachment_by_id(self, attachment_id: str) -> Optional[Attachment]:
        raise NotImplementedError

    @abstractmethod
    def archive_attachment(self, attachment_id: str) -> Attachment:
        """§7: قبل Submit فقط — Archive منطقي، بلا حذف الـAsset نفسه."""
        raise NotImplementedError


class PostgresMediaRepository(MediaRepository):
    """
    تنفيذ فعلي عبر PostgreSQL. غير مُختبَر على اتصال حي في هذه الجلسة (لا
    شبكة/محرك PostgreSQL متاح) — مكتوب بصياغة صحيحة نحويًا استنادًا لمخطط
    032_media_foundation.sql؛ يستوجب PostgreSQL Integration tests فعلية
    في CI قبل الاعتماد النهائي (نفس منهج كل خدمات Batch 1).
    """

    def __init__(self, connection):
        self._connection = connection

    def insert_asset(self, asset: Asset) -> Asset:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO media.assets (original_file_name, uploaded_by_user_ref_id, status) "
                "VALUES (%(name)s, %(uploader)s, %(status)s) RETURNING id, created_at",
                {"name": asset.original_file_name, "uploader": asset.uploaded_by_user_ref_id, "status": asset.status},
            )
            row = cur.fetchone()
            asset.id = row["id"]
            asset.created_at = row["created_at"]
        return asset

    def get_asset_by_id(self, asset_id: str) -> Optional[Asset]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, original_file_name, uploaded_by_user_ref_id, status, storage_key, "
                "storage_key_display, storage_key_thumbnail, mime_type, size_bytes, checksum, "
                "width, height, created_at, archived_at, purged_at FROM media.assets WHERE id = %(id)s",
                {"id": asset_id},
            )
            row = cur.fetchone()
        return self._row_to_asset(row) if row else None

    @staticmethod
    def _row_to_asset(row) -> Asset:
        return Asset(
            id=row["id"], original_file_name=row["original_file_name"],
            uploaded_by_user_ref_id=row["uploaded_by_user_ref_id"], status=row["status"],
            storage_key=row["storage_key"], storage_key_display=row["storage_key_display"],
            storage_key_thumbnail=row["storage_key_thumbnail"], mime_type=row["mime_type"],
            size_bytes=row["size_bytes"], checksum=row["checksum"], width=row["width"], height=row["height"],
            created_at=row["created_at"], archived_at=row["archived_at"], purged_at=row["purged_at"],
        )

    def update_asset_processing_result(
        self, asset_id: str, status: str,
        storage_key: Optional[str] = None, storage_key_display: Optional[str] = None,
        storage_key_thumbnail: Optional[str] = None, mime_type: Optional[str] = None,
        size_bytes: Optional[int] = None, checksum: Optional[str] = None,
        width: Optional[int] = None, height: Optional[int] = None,
    ) -> Asset:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE media.assets SET status = %(status)s, storage_key = %(storage_key)s, "
                    "storage_key_display = %(storage_key_display)s, storage_key_thumbnail = %(storage_key_thumbnail)s, "
                    "mime_type = %(mime_type)s, size_bytes = %(size_bytes)s, checksum = %(checksum)s, "
                    "width = %(width)s, height = %(height)s WHERE id = %(id)s",
                    {"id": asset_id, "status": status, "storage_key": storage_key,
                     "storage_key_display": storage_key_display, "storage_key_thumbnail": storage_key_thumbnail,
                     "mime_type": mime_type, "size_bytes": size_bytes, "checksum": checksum,
                     "width": width, "height": height},
                )
        return self.get_asset_by_id(asset_id)

    def archive_asset(self, asset_id: str) -> Asset:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE media.assets SET status = 'archived', archived_at = now() WHERE id = %(id)s",
                    {"id": asset_id},
                )
        return self.get_asset_by_id(asset_id)

    def list_unbound_ready_assets_older_than(self, cutoff_timestamp) -> List[Asset]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT a.id, a.original_file_name, a.uploaded_by_user_ref_id, a.status, a.storage_key, "
                "a.storage_key_display, a.storage_key_thumbnail, a.mime_type, a.size_bytes, a.checksum, "
                "a.width, a.height, a.created_at, a.archived_at, a.purged_at "
                "FROM media.assets a "
                "LEFT JOIN media.attachments att ON att.asset_ref_id = a.id "
                "WHERE a.status = 'ready' AND att.id IS NULL AND a.created_at < %(cutoff)s",
                {"cutoff": cutoff_timestamp},
            )
            rows = cur.fetchall()
        return [self._row_to_asset(r) for r in rows]

    def get_active_attachment_count_for_owner(self, owner_type: str, owner_ref_id: str) -> int:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM media.attachments "
                "WHERE owner_type = %(ot)s AND owner_ref_id = %(oid)s AND status = 'active'",
                {"ot": owner_type, "oid": owner_ref_id},
            )
            return cur.fetchone()["c"]

    def insert_attachment_with_lock(
        self, asset: Asset, owner_type: str, owner_ref_id: str,
        is_uploader_owner_checker,
    ) -> Attachment:
        from media_service import create_attachment  # تفادي دائرية الاستيراد عند تحميل الوحدة

        # §6/§17: namespace='media-binding' حرفيًا كما في الـBaseline. نطاق
        # القفل: (owner_type, owner_ref_id) — يخدم بالضبط ما يحتاج تسلسلًا
        # (فحص الحد + حساب sort_order لنفس Owner)، لا أوسع ولا أضيق.
        lock_key = compute_advisory_lock_key("media-binding", owner_type, owner_ref_id)

        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%(key)s)", {"key": lock_key})

                cur.execute(
                    "SELECT COUNT(*) AS c FROM media.attachments "
                    "WHERE owner_type = %(ot)s AND owner_ref_id = %(oid)s AND status = 'active'",
                    {"ot": owner_type, "oid": owner_ref_id},
                )
                current_count = cur.fetchone()["c"]

                cur.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_sort FROM media.attachments "
                    "WHERE owner_type = %(ot)s AND owner_ref_id = %(oid)s",
                    {"ot": owner_type, "oid": owner_ref_id},
                )
                next_sort_order = cur.fetchone()["next_sort"]

                # التحقق الكامل (Authorization + Limit) بعد القفل، على البيانات الحقيقية الآن
                attachment = create_attachment(
                    asset, owner_type, owner_ref_id, is_uploader_owner_checker, current_count, next_sort_order,
                )

                cur.execute(
                    "INSERT INTO media.attachments (asset_ref_id, owner_type, owner_ref_id, sort_order, status) "
                    "VALUES (%(asset_id)s, %(ot)s, %(oid)s, %(sort)s, 'active') RETURNING id, created_at",
                    {"asset_id": asset.id, "ot": owner_type, "oid": owner_ref_id, "sort": next_sort_order},
                )
                row = cur.fetchone()
                attachment.id = row["id"]
                attachment.created_at = row["created_at"]

        return attachment

    def list_attachments_for_owner(self, owner_type: str, owner_ref_id: str, status: str = "active") -> List[Attachment]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, asset_ref_id, owner_type, owner_ref_id, sort_order, status, created_at "
                "FROM media.attachments WHERE owner_type = %(ot)s AND owner_ref_id = %(oid)s AND status = %(status)s "
                "ORDER BY sort_order",
                {"ot": owner_type, "oid": owner_ref_id, "status": status},
            )
            rows = cur.fetchall()
        return [Attachment(id=r["id"], asset_ref_id=r["asset_ref_id"], owner_type=r["owner_type"],
                            owner_ref_id=r["owner_ref_id"], sort_order=r["sort_order"], status=r["status"],
                            created_at=r["created_at"]) for r in rows]

    def get_attachment_by_id(self, attachment_id: str) -> Optional[Attachment]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, asset_ref_id, owner_type, owner_ref_id, sort_order, status, created_at "
                "FROM media.attachments WHERE id = %(id)s",
                {"id": attachment_id},
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Attachment(id=row["id"], asset_ref_id=row["asset_ref_id"], owner_type=row["owner_type"],
                           owner_ref_id=row["owner_ref_id"], sort_order=row["sort_order"], status=row["status"],
                           created_at=row["created_at"])

    def archive_attachment(self, attachment_id: str) -> Attachment:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("UPDATE media.attachments SET status = 'archived' WHERE id = %(id)s", {"id": attachment_id})
        return self.get_attachment_by_id(attachment_id)


class InMemoryMediaRepository(MediaRepository):
    """تنفيذ وهمي للاختبار فقط. لا قفل حقيقي (أحادي الخيط)؛ نفس منطق العدّ/الترتيب المتسلسل."""

    def __init__(self):
        self._assets = {}
        self._attachments = {}
        self._seq = {"asset": 1, "attachment": 1}

    def insert_asset(self, asset: Asset) -> Asset:
        from datetime import datetime, timezone
        asset.id = f"asset-{self._seq['asset']}"
        self._seq["asset"] += 1
        asset.created_at = datetime.now(timezone.utc)
        self._assets[asset.id] = asset
        return asset

    def get_asset_by_id(self, asset_id: str) -> Optional[Asset]:
        return self._assets.get(asset_id)

    def update_asset_processing_result(
        self, asset_id: str, status: str,
        storage_key: Optional[str] = None, storage_key_display: Optional[str] = None,
        storage_key_thumbnail: Optional[str] = None, mime_type: Optional[str] = None,
        size_bytes: Optional[int] = None, checksum: Optional[str] = None,
        width: Optional[int] = None, height: Optional[int] = None,
    ) -> Asset:
        asset = self._assets[asset_id]
        asset.status = status
        if storage_key is not None: asset.storage_key = storage_key
        if storage_key_display is not None: asset.storage_key_display = storage_key_display
        if storage_key_thumbnail is not None: asset.storage_key_thumbnail = storage_key_thumbnail
        if mime_type is not None: asset.mime_type = mime_type
        if size_bytes is not None: asset.size_bytes = size_bytes
        if checksum is not None: asset.checksum = checksum
        if width is not None: asset.width = width
        if height is not None: asset.height = height
        return asset

    def archive_asset(self, asset_id: str) -> Asset:
        from datetime import datetime, timezone
        asset = self._assets[asset_id]
        asset.status = "archived"
        asset.archived_at = datetime.now(timezone.utc)
        return asset

    def list_unbound_ready_assets_older_than(self, cutoff_timestamp) -> List[Asset]:
        bound_asset_ids = {a.asset_ref_id for a in self._attachments.values()}
        return [
            a for a in self._assets.values()
            if a.status == "ready" and a.id not in bound_asset_ids and a.created_at < cutoff_timestamp
        ]

    def get_active_attachment_count_for_owner(self, owner_type: str, owner_ref_id: str) -> int:
        return sum(1 for a in self._attachments.values()
                   if a.owner_type == owner_type and a.owner_ref_id == owner_ref_id and a.status == "active")

    def insert_attachment_with_lock(
        self, asset: Asset, owner_type: str, owner_ref_id: str,
        is_uploader_owner_checker,
    ) -> Attachment:
        from datetime import datetime, timezone
        from media_service import create_attachment

        current_count = self.get_active_attachment_count_for_owner(owner_type, owner_ref_id)
        existing_sort_orders = [a.sort_order for a in self._attachments.values()
                                 if a.owner_type == owner_type and a.owner_ref_id == owner_ref_id]
        next_sort_order = (max(existing_sort_orders) + 1) if existing_sort_orders else 0

        attachment = create_attachment(
            asset, owner_type, owner_ref_id, is_uploader_owner_checker, current_count, next_sort_order,
        )
        attachment.id = f"attachment-{self._seq['attachment']}"
        self._seq["attachment"] += 1
        attachment.created_at = datetime.now(timezone.utc)
        self._attachments[attachment.id] = attachment
        return attachment

    def list_attachments_for_owner(self, owner_type: str, owner_ref_id: str, status: str = "active") -> List[Attachment]:
        result = [a for a in self._attachments.values()
                  if a.owner_type == owner_type and a.owner_ref_id == owner_ref_id and a.status == status]
        return sorted(result, key=lambda a: a.sort_order)

    def get_attachment_by_id(self, attachment_id: str) -> Optional[Attachment]:
        return self._attachments.get(attachment_id)

    def archive_attachment(self, attachment_id: str) -> Attachment:
        attachment = self._attachments[attachment_id]
        attachment.status = "archived"
        return attachment
