"""
vct_repository.py — طبقة الوصول للبيانات لخدمة كتالوج السيارات (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.4؛ 005_vct.sql
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from vct_service import (
    Manufacturer, Model, Generation, Trim, TrimModelYear, MarketAvailability,
    MarketAvailabilityLevelConflictError,
)
from advisory_lock import compute_advisory_lock_key


class VctRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه vct_service.py."""

    @abstractmethod
    def insert_manufacturer(self, manufacturer: Manufacturer) -> Manufacturer:
        raise NotImplementedError

    @abstractmethod
    def update_manufacturer(self, manufacturer: Manufacturer) -> Manufacturer:
        raise NotImplementedError

    @abstractmethod
    def get_manufacturer_by_id(self, manufacturer_id: str) -> Optional[Manufacturer]:
        raise NotImplementedError

    @abstractmethod
    def insert_model(self, model: Model) -> Model:
        raise NotImplementedError

    @abstractmethod
    def get_model_by_id(self, model_id: str) -> Optional[Model]:
        """امتداد VCT Contract Extension: كانت مفقودة رغم وجود insert_model."""
        raise NotImplementedError

    @abstractmethod
    def insert_generation(self, generation: Generation) -> Generation:
        raise NotImplementedError

    @abstractmethod
    def get_generation_by_id(self, generation_id: str) -> Optional[Generation]:
        """امتداد VCT Contract Extension: كانت مفقودة رغم وجود insert_generation."""
        raise NotImplementedError

    @abstractmethod
    def insert_trim(self, trim: Trim) -> Trim:
        raise NotImplementedError

    @abstractmethod
    def get_trim_by_id(self, trim_id: str) -> Optional[Trim]:
        raise NotImplementedError

    @abstractmethod
    def is_trim_valid(self, trim_id: str) -> bool:
        """نقطة التكامل الرسمية التي تستهلكها خدمة CMP عبر حقن الاعتمادية."""
        raise NotImplementedError

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §2-4: Generation years + Trim Model Years
    # -----------------------------------------------------------------

    @abstractmethod
    def update_generation(self, generation: Generation) -> Generation:
        raise NotImplementedError

    @abstractmethod
    def get_generation_year_range_for_trim(self, trim_ref_id: str) -> Optional[tuple]:
        """يحل trim_ref_id → generation_id ويُعيد (start_year, end_year)، أو None إن لم توجد الفئة."""
        raise NotImplementedError

    @abstractmethod
    def insert_trim_model_year(self, tmy: TrimModelYear) -> TrimModelYear:
        raise NotImplementedError

    @abstractmethod
    def get_trim_model_year_by_id(self, tmy_id: str) -> Optional[TrimModelYear]:
        raise NotImplementedError

    @abstractmethod
    def list_trim_model_years_for_trim(self, trim_ref_id: str) -> List[TrimModelYear]:
        raise NotImplementedError

    @abstractmethod
    def list_trim_model_years_for_generation(self, generation_id: str) -> List[int]:
        """يُعيد كل السنوات (أرقامًا فقط) عبر كل فئات (trims) هذا الجيل — لازمة لفحص §4 عند تعديل نطاق الجيل."""
        raise NotImplementedError

    @abstractmethod
    def get_trim_ref_id_for_trim_model_year(self, trim_model_year_id: str) -> Optional[str]:
        """§16-17: Resolve الأساسي لأي Year-specific target إلى trim_ref_id الأصلي قبل القفل."""
        raise NotImplementedError

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §6-9، 17: Market Availability
    # -----------------------------------------------------------------

    @abstractmethod
    def insert_market_availability_with_lock(
        self, country_ref_id: str,
        trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
    ) -> MarketAvailability:
        """
        §17: يحل الهدف إلى trim_ref_id الأصلي، يحصل على Advisory Lock
        بنطاق (vct-market, resolved_trim_ref_id)، يعيد فحص وجود صفوف من
        المستوى الآخر لنفس الفئة (منع التعايش §8)، ثم يُدرِج — كل ذلك ضمن
        نفس Transaction. يرفع MarketAvailabilityLevelConflictError عند
        اكتشاف تعايش.
        """
        raise NotImplementedError

    @abstractmethod
    def get_market_availability_for_target(
        self, trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
    ) -> List[MarketAvailability]:
        raise NotImplementedError

    # -----------------------------------------------------------------
    # Batch 1 (Frontend Enablement): قوائم تصفح عامة — بلا جلسة (نفس طبيعة
    # Search العامة). تُعيد قواميس بسيطة {id, name, ...} لا كائنات
    # Manufacturer/Model/... الأصلية (تلك للاستهلاك الإداري/CRUD فقط).
    # -----------------------------------------------------------------

    @abstractmethod
    def list_approved_manufacturers(self) -> List[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_approved_models_for_manufacturer(self, manufacturer_id: str) -> List[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_generations_for_model(self, model_id: str) -> List[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_trims_for_generation(self, generation_id: str) -> List[dict]:
        raise NotImplementedError


class PostgresVctRepository(VctRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 005_vct.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_manufacturer(self, manufacturer: Manufacturer) -> Manufacturer:
        with self._connection.cursor() as cur:
            cur.execute("INSERT INTO vct.manufacturers (status) VALUES (%(status)s) RETURNING id",
                        {"status": manufacturer.status})
            manufacturer.id = cur.fetchone()["id"]
        return manufacturer

    def update_manufacturer(self, manufacturer: Manufacturer) -> Manufacturer:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("UPDATE vct.manufacturers SET status = %(status)s, updated_at = now() WHERE id = %(id)s",
                            {"status": manufacturer.status, "id": manufacturer.id})
        return manufacturer

    def get_manufacturer_by_id(self, manufacturer_id: str) -> Optional[Manufacturer]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, status FROM vct.manufacturers WHERE id = %(id)s", {"id": manufacturer_id})
            row = cur.fetchone()
        return Manufacturer(id=row["id"], status=row["status"]) if row else None

    def insert_model(self, model: Model) -> Model:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO vct.models (manufacturer_id, status) VALUES (%(manufacturer_id)s, %(status)s) RETURNING id",
                {"manufacturer_id": model.manufacturer_id, "status": model.status},
            )
            model.id = cur.fetchone()["id"]
        return model

    def get_model_by_id(self, model_id: str) -> Optional[Model]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, manufacturer_id, status FROM vct.models WHERE id = %(id)s", {"id": model_id})
            row = cur.fetchone()
        return Model(id=row["id"], manufacturer_id=row["manufacturer_id"], status=row["status"]) if row else None

    def insert_generation(self, generation: Generation) -> Generation:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO vct.generations (model_id, start_year, end_year) "
                "VALUES (%(model_id)s, %(start_year)s, %(end_year)s) RETURNING id",
                {"model_id": generation.model_id, "start_year": generation.start_year, "end_year": generation.end_year},
            )
            generation.id = cur.fetchone()["id"]
        return generation

    def get_generation_by_id(self, generation_id: str) -> Optional[Generation]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, model_id, start_year, end_year FROM vct.generations WHERE id = %(id)s",
                        {"id": generation_id})
            row = cur.fetchone()
        return Generation(id=row["id"], model_id=row["model_id"],
                           start_year=row["start_year"], end_year=row["end_year"]) if row else None

    def update_generation(self, generation: Generation) -> Generation:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE vct.generations SET start_year = %(start_year)s, end_year = %(end_year)s, "
                    "updated_at = now() WHERE id = %(id)s",
                    {"start_year": generation.start_year, "end_year": generation.end_year, "id": generation.id},
                )
        return generation

    def insert_trim(self, trim: Trim) -> Trim:
        # يعتمد على idx_trims_generation_id
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO vct.trims (generation_id, fuel_type_ref_id, transmission_type_ref_id) "
                "VALUES (%(generation_id)s, %(fuel_type_ref_id)s, %(transmission_type_ref_id)s) RETURNING id",
                {"generation_id": trim.generation_id, "fuel_type_ref_id": trim.fuel_type_ref_id,
                 "transmission_type_ref_id": trim.transmission_type_ref_id},
            )
            trim.id = cur.fetchone()["id"]
        return trim

    def get_trim_by_id(self, trim_id: str) -> Optional[Trim]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, generation_id, fuel_type_ref_id, transmission_type_ref_id FROM vct.trims WHERE id = %(id)s",
                {"id": trim_id},
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Trim(id=row["id"], generation_id=row["generation_id"],
                     fuel_type_ref_id=row["fuel_type_ref_id"], transmission_type_ref_id=row["transmission_type_ref_id"])

    def is_trim_valid(self, trim_id: str) -> bool:
        return self.get_trim_by_id(trim_id) is not None

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §3-4: Trim Model Years
    # -----------------------------------------------------------------

    def get_generation_year_range_for_trim(self, trim_ref_id: str) -> Optional[tuple]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT g.start_year, g.end_year FROM vct.trims t "
                "JOIN vct.generations g ON g.id = t.generation_id WHERE t.id = %(trim_id)s",
                {"trim_id": trim_ref_id},
            )
            row = cur.fetchone()
        return (row["start_year"], row["end_year"]) if row else None

    def insert_trim_model_year(self, tmy: TrimModelYear) -> TrimModelYear:
        # يعتمد على uq_trim_model_years_trim_year (يمنع أي تكرار متسابق فعليًا على مستوى DB)
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO vct.trim_model_years (trim_ref_id, year) VALUES (%(trim_ref_id)s, %(year)s) RETURNING id",
                {"trim_ref_id": tmy.trim_ref_id, "year": tmy.year},
            )
            tmy.id = cur.fetchone()["id"]
        return tmy

    def get_trim_model_year_by_id(self, tmy_id: str) -> Optional[TrimModelYear]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, trim_ref_id, year FROM vct.trim_model_years WHERE id = %(id)s", {"id": tmy_id})
            row = cur.fetchone()
        return TrimModelYear(id=row["id"], trim_ref_id=row["trim_ref_id"], year=row["year"]) if row else None

    def list_trim_model_years_for_trim(self, trim_ref_id: str) -> List[TrimModelYear]:
        # يعتمد على idx_trim_model_years_trim_id
        with self._connection.cursor() as cur:
            cur.execute("SELECT id, trim_ref_id, year FROM vct.trim_model_years WHERE trim_ref_id = %(trim_id)s",
                        {"trim_id": trim_ref_id})
            rows = cur.fetchall()
        return [TrimModelYear(id=r["id"], trim_ref_id=r["trim_ref_id"], year=r["year"]) for r in rows]

    def list_trim_model_years_for_generation(self, generation_id: str) -> List[int]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT tmy.year FROM vct.trim_model_years tmy "
                "JOIN vct.trims t ON t.id = tmy.trim_ref_id WHERE t.generation_id = %(generation_id)s",
                {"generation_id": generation_id},
            )
            rows = cur.fetchall()
        return [r["year"] for r in rows]

    def get_trim_ref_id_for_trim_model_year(self, trim_model_year_id: str) -> Optional[str]:
        tmy = self.get_trim_model_year_by_id(trim_model_year_id)
        return tmy.trim_ref_id if tmy else None

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §6-9، 17: Market Availability
    # -----------------------------------------------------------------

    def insert_market_availability_with_lock(
        self, country_ref_id: str,
        trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
    ) -> MarketAvailability:
        # §17: Resolve دائمًا إلى underlying trim_ref_id أولًا (حتى لو كان
        # الهدف Year-specific)، ثم القفل بنطاق (vct-market, resolved_trim)
        # — بلا country في مفتاح القفل عمدًا: الهدف منع تعايش Trim-level/
        # Year-specific لنفس الفئة عبر كل الدول معًا، لا لكل دولة بمعزل عن
        # الأخريات (والذي كان سيسمح بتسابق عبر دول مختلفة، §8/§17).
        if trim_ref_id is not None:
            resolved_trim_ref_id = trim_ref_id
        else:
            resolved_trim_ref_id = self.get_trim_ref_id_for_trim_model_year(trim_model_year_ref_id)
            if resolved_trim_ref_id is None:
                raise ValueError(f"لا توجد سنة موديل بالمعرّف: {trim_model_year_ref_id}")

        lock_key = compute_advisory_lock_key("vct-market", resolved_trim_ref_id)

        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%(key)s)", {"key": lock_key})

                # إعادة الفحص الفعلي داخل نفس Transaction بعد الحصول على القفل (§16 الأخيرة)
                cur.execute(
                    "SELECT COUNT(*) AS c FROM vct.trim_market_availability WHERE trim_ref_id = %(trim_id)s",
                    {"trim_id": resolved_trim_ref_id},
                )
                trim_level_count = cur.fetchone()["c"]
                cur.execute(
                    "SELECT COUNT(*) AS c FROM vct.trim_market_availability tma "
                    "JOIN vct.trim_model_years tmy ON tmy.id = tma.trim_model_year_ref_id "
                    "WHERE tmy.trim_ref_id = %(trim_id)s",
                    {"trim_id": resolved_trim_ref_id},
                )
                year_level_count = cur.fetchone()["c"]

                if trim_ref_id is not None and year_level_count > 0:
                    raise MarketAvailabilityLevelConflictError(
                        "توجد بالفعل صفوف Market Availability بمستوى السنة (Year-specific) لهذه الفئة؛ "
                        "لا يجوز إضافة صف Trim-level لنفس الفئة (§8)."
                    )
                if trim_model_year_ref_id is not None and trim_level_count > 0:
                    raise MarketAvailabilityLevelConflictError(
                        "توجد بالفعل صفوف Market Availability بمستوى الفئة (Trim-level) لهذه الفئة؛ "
                        "لا يجوز إضافة صف Year-specific لنفس الفئة (§8)."
                    )

                cur.execute(
                    "INSERT INTO vct.trim_market_availability (trim_ref_id, trim_model_year_ref_id, country_ref_id) "
                    "VALUES (%(trim_ref_id)s, %(trim_model_year_ref_id)s, %(country_ref_id)s) RETURNING id",
                    {"trim_ref_id": trim_ref_id, "trim_model_year_ref_id": trim_model_year_ref_id,
                     "country_ref_id": country_ref_id},
                )
                new_id = cur.fetchone()["id"]

        return MarketAvailability(id=new_id, country_ref_id=country_ref_id,
                                   trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id)

    def get_market_availability_for_target(
        self, trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
    ) -> List[MarketAvailability]:
        with self._connection.cursor() as cur:
            if trim_ref_id is not None:
                cur.execute(
                    "SELECT id, trim_ref_id, trim_model_year_ref_id, country_ref_id "
                    "FROM vct.trim_market_availability WHERE trim_ref_id = %(trim_id)s",
                    {"trim_id": trim_ref_id},
                )
            else:
                cur.execute(
                    "SELECT id, trim_ref_id, trim_model_year_ref_id, country_ref_id "
                    "FROM vct.trim_market_availability WHERE trim_model_year_ref_id = %(tmy_id)s",
                    {"tmy_id": trim_model_year_ref_id},
                )
            rows = cur.fetchall()
        return [MarketAvailability(id=r["id"], trim_ref_id=r["trim_ref_id"],
                                    trim_model_year_ref_id=r["trim_model_year_ref_id"],
                                    country_ref_id=r["country_ref_id"]) for r in rows]

    # -----------------------------------------------------------------
    # Batch 1 (Frontend Enablement): قوائم تصفح عامة
    # -----------------------------------------------------------------

    _NAME_LATERAL_SQL = """
        LEFT JOIN LATERAL (
            SELECT name_value FROM vct.localized_names
            WHERE owner_ref_id = {alias}.id AND owner_type = '{owner_type}'
            ORDER BY locale NULLS FIRST, id LIMIT 1
        ) {alias}ln ON true
    """

    def list_approved_manufacturers(self) -> List[dict]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT m.id, mln.name_value AS name FROM vct.manufacturers m "
                + self._NAME_LATERAL_SQL.format(alias="m", owner_type="manufacturer")
                + " WHERE m.status = 'approved' ORDER BY mln.name_value NULLS LAST, m.id"
            )
            rows = cur.fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    def list_approved_models_for_manufacturer(self, manufacturer_id: str) -> List[dict]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT mo.id, moln.name_value AS name FROM vct.models mo "
                + self._NAME_LATERAL_SQL.format(alias="mo", owner_type="model")
                + " WHERE mo.status = 'approved' AND mo.manufacturer_id = %(mid)s ORDER BY moln.name_value NULLS LAST, mo.id",
                {"mid": manufacturer_id},
            )
            rows = cur.fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    def list_generations_for_model(self, model_id: str) -> List[dict]:
        # لا status على الجيل (لا دورة حياة اعتماد منفصلة له في هذا الإصدار)
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT g.id, gln.name_value AS name, g.start_year, g.end_year FROM vct.generations g "
                + self._NAME_LATERAL_SQL.format(alias="g", owner_type="generation")
                + " WHERE g.model_id = %(mid)s ORDER BY g.start_year NULLS LAST, g.id",
                {"mid": model_id},
            )
            rows = cur.fetchall()
        return [{"id": r["id"], "name": r["name"], "start_year": r["start_year"], "end_year": r["end_year"]} for r in rows]

    def list_trims_for_generation(self, generation_id: str) -> List[dict]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT t.id, tln.name_value AS name, t.fuel_type_ref_id, t.transmission_type_ref_id "
                "FROM vct.trims t "
                + self._NAME_LATERAL_SQL.format(alias="t", owner_type="trim")
                + " WHERE t.generation_id = %(gid)s ORDER BY tln.name_value NULLS LAST, t.id",
                {"gid": generation_id},
            )
            rows = cur.fetchall()
        return [{"id": r["id"], "name": r["name"], "fuel_type_ref_id": r["fuel_type_ref_id"],
                  "transmission_type_ref_id": r["transmission_type_ref_id"]} for r in rows]


class InMemoryVctRepository(VctRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._manufacturers = {}
        self._models = {}
        self._generations = {}
        self._trims = {}
        self._trim_model_years = {}
        self._market_availability = {}
        self._seq = {"manufacturer": 1, "model": 1, "generation": 1, "trim": 1, "tmy": 1, "market": 1}
        self._localized_names = {}  # (owner_type, owner_id) -> name، Batch 1 Frontend Enablement

    def insert_manufacturer(self, manufacturer: Manufacturer) -> Manufacturer:
        manufacturer.id = f"manufacturer-{self._seq['manufacturer']}"
        self._seq["manufacturer"] += 1
        self._manufacturers[manufacturer.id] = manufacturer
        return manufacturer

    def update_manufacturer(self, manufacturer: Manufacturer) -> Manufacturer:
        self._manufacturers[manufacturer.id] = manufacturer
        return manufacturer

    def get_manufacturer_by_id(self, manufacturer_id: str) -> Optional[Manufacturer]:
        return self._manufacturers.get(manufacturer_id)

    def insert_model(self, model: Model) -> Model:
        model.id = f"model-{self._seq['model']}"
        self._seq["model"] += 1
        self._models[model.id] = model
        return model

    def get_model_by_id(self, model_id: str) -> Optional[Model]:
        return self._models.get(model_id)

    def insert_generation(self, generation: Generation) -> Generation:
        generation.id = f"generation-{self._seq['generation']}"
        self._seq["generation"] += 1
        self._generations[generation.id] = generation
        return generation

    def get_generation_by_id(self, generation_id: str) -> Optional[Generation]:
        return self._generations.get(generation_id)

    def insert_trim(self, trim: Trim) -> Trim:
        trim.id = f"trim-{self._seq['trim']}"
        self._seq["trim"] += 1
        self._trims[trim.id] = trim
        return trim

    def get_trim_by_id(self, trim_id: str) -> Optional[Trim]:
        return self._trims.get(trim_id)

    def is_trim_valid(self, trim_id: str) -> bool:
        return trim_id in self._trims

    def update_generation(self, generation: Generation) -> Generation:
        self._generations[generation.id] = generation
        return generation

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §3-4: Trim Model Years
    # -----------------------------------------------------------------

    def get_generation_year_range_for_trim(self, trim_ref_id: str) -> Optional[tuple]:
        trim = self._trims.get(trim_ref_id)
        if trim is None:
            return None
        generation = self._generations.get(trim.generation_id)
        if generation is None:
            return None
        return (generation.start_year, generation.end_year)

    def insert_trim_model_year(self, tmy: TrimModelYear) -> TrimModelYear:
        tmy.id = f"tmy-{self._seq['tmy']}"
        self._seq["tmy"] += 1
        self._trim_model_years[tmy.id] = tmy
        return tmy

    def get_trim_model_year_by_id(self, tmy_id: str) -> Optional[TrimModelYear]:
        return self._trim_model_years.get(tmy_id)

    def list_trim_model_years_for_trim(self, trim_ref_id: str) -> List[TrimModelYear]:
        return [t for t in self._trim_model_years.values() if t.trim_ref_id == trim_ref_id]

    def list_trim_model_years_for_generation(self, generation_id: str) -> List[int]:
        trim_ids_in_generation = {t.id for t in self._trims.values() if t.generation_id == generation_id}
        return [tmy.year for tmy in self._trim_model_years.values() if tmy.trim_ref_id in trim_ids_in_generation]

    def get_trim_ref_id_for_trim_model_year(self, trim_model_year_id: str) -> Optional[str]:
        tmy = self._trim_model_years.get(trim_model_year_id)
        return tmy.trim_ref_id if tmy else None

    # -----------------------------------------------------------------
    # Approved VCT Design Baseline §6-9، 17: Market Availability
    # لا قفل حقيقي هنا (تنفيذ وهمي أحادي الخيط)؛ نفس قاعدة منع التعايش
    # مُطبَّقة مباشرة بلا حاجة لـAdvisory Lock (لا تزامن فعلي في الاختبار).
    # -----------------------------------------------------------------

    def insert_market_availability_with_lock(
        self, country_ref_id: str,
        trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
    ) -> MarketAvailability:
        if trim_ref_id is not None:
            resolved_trim_ref_id = trim_ref_id
        else:
            resolved_trim_ref_id = self.get_trim_ref_id_for_trim_model_year(trim_model_year_ref_id)
            if resolved_trim_ref_id is None:
                raise ValueError(f"لا توجد سنة موديل بالمعرّف: {trim_model_year_ref_id}")

        trim_level_rows = [m for m in self._market_availability.values() if m.trim_ref_id == resolved_trim_ref_id]
        year_level_rows = [
            m for m in self._market_availability.values()
            if m.trim_model_year_ref_id is not None
            and self._trim_model_years.get(m.trim_model_year_ref_id) is not None
            and self._trim_model_years[m.trim_model_year_ref_id].trim_ref_id == resolved_trim_ref_id
        ]

        if trim_ref_id is not None and year_level_rows:
            raise MarketAvailabilityLevelConflictError(
                "توجد بالفعل صفوف Market Availability بمستوى السنة (Year-specific) لهذه الفئة؛ "
                "لا يجوز إضافة صف Trim-level لنفس الفئة (§8)."
            )
        if trim_model_year_ref_id is not None and trim_level_rows:
            raise MarketAvailabilityLevelConflictError(
                "توجد بالفعل صفوف Market Availability بمستوى الفئة (Trim-level) لهذه الفئة؛ "
                "لا يجوز إضافة صف Year-specific لنفس الفئة (§8)."
            )

        entry = MarketAvailability(id=f"market-{self._seq['market']}", country_ref_id=country_ref_id,
                                    trim_ref_id=trim_ref_id, trim_model_year_ref_id=trim_model_year_ref_id)
        self._seq["market"] += 1
        self._market_availability[entry.id] = entry
        return entry

    def get_market_availability_for_target(
        self, trim_ref_id: Optional[str] = None, trim_model_year_ref_id: Optional[str] = None,
    ) -> List[MarketAvailability]:
        if trim_ref_id is not None:
            return [m for m in self._market_availability.values() if m.trim_ref_id == trim_ref_id]
        return [m for m in self._market_availability.values() if m.trim_model_year_ref_id == trim_model_year_ref_id]

    # -----------------------------------------------------------------
    # Batch 1 (Frontend Enablement): قوائم تصفح عامة — مبسَّطة (لا localized_names
    # حقيقية في هذا التنفيذ الوهمي)؛ name=None افتراضيًا ما لم يُزرَع صراحةً عبر
    # seed_localized_name_for_testing. السلوك الفعلي (الأسماء الحقيقية) يُختبَر
    # حصرًا على PostgreSQL حي، بنفس نمط كل حقول الأسماء الأخرى في هذه الدفعة.
    # -----------------------------------------------------------------

    def list_approved_manufacturers(self) -> List[dict]:
        return [{"id": m.id, "name": self._localized_names.get(("manufacturer", m.id))}
                for m in self._manufacturers.values() if m.status == "approved"]

    def list_approved_models_for_manufacturer(self, manufacturer_id: str) -> List[dict]:
        return [{"id": mo.id, "name": self._localized_names.get(("model", mo.id))}
                for mo in self._models.values() if mo.status == "approved" and mo.manufacturer_id == manufacturer_id]

    def list_generations_for_model(self, model_id: str) -> List[dict]:
        return [{"id": g.id, "name": self._localized_names.get(("generation", g.id)),
                  "start_year": g.start_year, "end_year": g.end_year}
                for g in self._generations.values() if g.model_id == model_id]

    def list_trims_for_generation(self, generation_id: str) -> List[dict]:
        return [{"id": t.id, "name": self._localized_names.get(("trim", t.id)),
                  "fuel_type_ref_id": t.fuel_type_ref_id, "transmission_type_ref_id": t.transmission_type_ref_id}
                for t in self._trims.values() if t.generation_id == generation_id]

    def seed_localized_name_for_testing(self, owner_type: str, owner_id: str, name: str) -> None:
        """اختباري فقط: يزرع اسمًا محلولًا لعنصر VCT لاختبار مسارات القوائم بأسماء حقيقية."""
        self._localized_names[(owner_type, owner_id)] = name

    def seed_trim_for_testing(
        self, trim_id: str, generation_id: str = "generation-test",
        fuel_type_ref_id: str = "fuel-test", transmission_type_ref_id: str = "trans-test",
    ) -> Trim:
        """
        اختباري فقط (لا استخدام إنتاجي): يُدرِج فئة سيارة مباشرة بمعرِّف
        محدَّد سلفًا، بلا سلسلة manufacturer→model→generation الكاملة —
        لتفادي تكرار نفس الإعداد الطويل في كل اختبار Purchase Request
        القائم لا يحتاج فعليًا التحقق من تفاصيل الفئة نفسها. الفئة الناتجة
        حقيقية وصالحة (is_trim_valid تُعيد True لها) — الفرق الوحيد شكلي
        (تخطي سلسلة الإنشاء الطويلة)، لا تحايل على التحقق نفسه.
        """
        trim = Trim(id=trim_id, generation_id=generation_id,
                    fuel_type_ref_id=fuel_type_ref_id, transmission_type_ref_id=transmission_type_ref_id)
        self._trims[trim_id] = trim
        return trim
