"""Json Schema Validation against integrations/ingestion_contract/v1.schema.json.

Ensures that the parsed output conforms to the architecture's data contract
before it is written or indexed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "integrations" / "ingestion_contract" / "v1.schema.json"


def load_manifest_schema() -> dict[str, Any]:
    """Load the v1.schema.json file."""
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found at {_SCHEMA_PATH}")
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate_manifest(data: dict[str, Any]) -> None:
    """Validate a full ingestion manifest against the schema.

    Raises jsonschema.ValidationError if validation fails.
    """
    schema = load_manifest_schema()
    jsonschema.validate(instance=data, schema=schema)


def validate_manifest_item(item: dict[str, Any]) -> None:
    """Validate a single item from the items list of the manifest."""
    schema = load_manifest_schema()
    item_schema = schema["properties"]["items"]["items"]
    jsonschema.validate(instance=item, schema=item_schema)
