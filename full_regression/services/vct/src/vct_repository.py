"""
vct_repository.py — طبقة الوصول للبيانات لخدمة كتالوج السيارات (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.4؛ 005_vct.sql
"""

from abc import ABC, abstractmethod
from typing import Optional

from vct_service import Manufacturer, Model, Generation, Trim


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
    def insert_generation(self, generation: Generation) -> Generation:
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

    def insert_generation(self, generation: Generation) -> Generation:
        with self._connection.cursor() as cur:
            cur.execute("INSERT INTO vct.generations (model_id) VALUES (%(model_id)s) RETURNING id",
                        {"model_id": generation.model_id})
            generation.id = cur.fetchone()["id"]
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


class InMemoryVctRepository(VctRepository):
    """تنفيذ وهمي للاختبار فقط."""

    def __init__(self):
        self._manufacturers = {}
        self._models = {}
        self._generations = {}
        self._trims = {}
        self._seq = {"manufacturer": 1, "model": 1, "generation": 1, "trim": 1}

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

    def insert_generation(self, generation: Generation) -> Generation:
        generation.id = f"generation-{self._seq['generation']}"
        self._seq["generation"] += 1
        self._generations[generation.id] = generation
        return generation

    def insert_trim(self, trim: Trim) -> Trim:
        trim.id = f"trim-{self._seq['trim']}"
        self._seq["trim"] += 1
        self._trims[trim.id] = trim
        return trim

    def get_trim_by_id(self, trim_id: str) -> Optional[Trim]:
        return self._trims.get(trim_id)

    def is_trim_valid(self, trim_id: str) -> bool:
        return trim_id in self._trims
