import json
from pathlib import Path
from typing import Any
import jsonschema

# Path resolution: find the directory containing both backend and pmc-pipeline
# __file__ is backend/src/evidence_platform/modules/ingestion/manifest_validator.py
# parents[0] = ingestion/
# parents[1] = modules/
# parents[2] = evidence_platform/
# parents[3] = src/
# parents[4] = backend/
# parents[5] = Indence (Workspace Root)
WORKSPACE_ROOT = Path(__file__).resolve().parents[5]
_SCHEMA_PATH = WORKSPACE_ROOT / "pmc-pipeline" / "integrations" / "ingestion_contract" / "v1.schema.json"

def load_manifest_schema() -> dict[str, Any]:
    """Load the v1.schema.json file."""
    schema_path = _SCHEMA_PATH
    
    # Fallback to local copy in backend/ if not found in pmc-pipeline/
    if not schema_path.exists():
        schema_path = WORKSPACE_ROOT / "backend" / "integrations" / "ingestion_contract" / "v1.schema.json"
        
    if not schema_path.exists():
        raise FileNotFoundError(f"Ingestion schema file not found. Checked: {_SCHEMA_PATH} and {schema_path}")
        
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)

def validate_manifest(data: dict[str, Any]) -> None:
    """Validate a full ingestion manifest against the schema."""
    schema = load_manifest_schema()
    jsonschema.validate(instance=data, schema=schema)

def validate_manifest_item(item: dict[str, Any]) -> None:
    """Validate a single item from the items list of the manifest."""
    schema = load_manifest_schema()
    item_schema = schema["properties"]["items"]["items"]
    jsonschema.validate(instance=item, schema=item_schema)
