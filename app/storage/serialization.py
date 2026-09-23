from __future__ import annotations

from dataclasses import asdict

from app.core.enums import PIIType
from app.core.models import EntityMapping


def mapping_to_dict(mapping: EntityMapping) -> dict:
    data = asdict(mapping)
    data["type"] = mapping.type.value
    return data


def mapping_from_dict(data: dict) -> EntityMapping:
    data = dict(data)
    data["type"] = PIIType(data["type"])
    return EntityMapping(**data)
