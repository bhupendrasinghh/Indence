"""Configuration loader — YAML to typed dataclasses.

Loads ``config.yaml`` (or a user-specified path) and exposes every section
as a frozen, type-hinted dataclass so the rest of the pipeline can rely on
IDE auto-complete and static analysis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NCBIConfig:
    """NCBI API credentials and rate-limit settings."""

    api_key: str = ""
    email: str = ""
    tool_name: str = "pmc_oncology_pipeline"
    rate_limit: int = 3  # bumped to 10 when api_key is present


@dataclass
class PipelineConfig:
    """Tuning knobs for the download pipeline."""

    max_workers: int = 50
    batch_size_search: int = 10_000
    batch_size_id_convert: int = 200
    batch_size_oa_check: int = 25
    batch_size_download: int = 50
    max_retries: int = 5
    retry_base_delay: float = 2.0
    retry_max_delay: float = 120.0
    request_timeout: int = 60
    download_timeout: int = 300
    discovery_limit: int | None = None
    download_limit: int = 10_000


@dataclass
class SearchConfig:
    """PubMed search parameters."""

    date_range_years: int = 5
    queries: dict[str, str] = field(default_factory=dict)


@dataclass
class SpeciesConfig:
    """Species filtering parameters."""

    allow_mixed_species: bool = False
    allow_unindexed_species: bool = True


@dataclass
class PathsConfig:
    """Filesystem paths (resolved relative to the config-file directory)."""

    data_dir: str = "data"
    metadata_dir: str = "data/metadata"
    xml_dir: str = "data/xml"
    json_dir: str = "data/json"
    logs_dir: str = "data/logs"
    checkpoints_dir: str = "data/checkpoints"
    database: str = "data/metadata/papers.db"

    # Populated by ``_resolve`` — not part of YAML
    _root: str = field(default="", repr=False)

    # -- helpers ----------------------------------------------------------

    def _resolve(self, root: str | Path) -> None:
        """Make every path absolute (relative to *root*)."""
        self._root = str(root)
        for attr in (
            "data_dir",
            "metadata_dir",
            "xml_dir",
            "json_dir",
            "logs_dir",
            "checkpoints_dir",
            "database",
        ):
            val = getattr(self, attr)
            if not os.path.isabs(val):
                setattr(self, attr, str(Path(root) / val))

    def ensure_dirs(self) -> None:
        """Create every directory that doesn't yet exist."""
        for attr in (
            "data_dir",
            "metadata_dir",
            "xml_dir",
            "json_dir",
            "logs_dir",
            "checkpoints_dir",
        ):
            Path(getattr(self, attr)).mkdir(parents=True, exist_ok=True)


@dataclass
class AppConfig:
    """Top-level configuration container."""

    ncbi: NCBIConfig = field(default_factory=NCBIConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    species: SpeciesConfig = field(default_factory=SpeciesConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    excluded_article_types: list[str] = field(default_factory=list)
    allowed_article_types: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _dict_to_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Recursively map a raw dict to a dataclass, ignoring unknown keys."""
    if data is None:
        return cls()
    known = {f.name for f in cls.__dataclass_fields__.values() if not f.name.startswith("_")}
    filtered = {k: v for k, v in data.items() if k in known}
    return cls(**filtered)


def load_config(path: str = "config.yaml") -> AppConfig:
    """Load *path* and return a fully-resolved :class:`AppConfig`.

    Paths inside the ``paths`` section are resolved relative to the
    directory containing *path* itself.
    """
    config_path = Path(path).resolve()

    # Load .env manually if it exists in the config directory or current directory
    for dotenv_dir in (config_path.parent, Path.cwd()):
        dotenv_path = dotenv_dir / ".env"
        if dotenv_path.exists():
            try:
                with open(dotenv_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            k, v = parts[0].strip(), parts[1].strip()
                            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                                v = v[1:-1]
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Please copy config.yaml.example to config.yaml and configure it, or define environment variables."
        )

    with open(config_path, encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh)

    cfg = AppConfig(
        ncbi=_dict_to_dataclass(NCBIConfig, raw.get("ncbi", {})),
        pipeline=_dict_to_dataclass(PipelineConfig, raw.get("pipeline", {})),
        search=_dict_to_dataclass(SearchConfig, raw.get("search", {})),
        species=_dict_to_dataclass(SpeciesConfig, raw.get("species", {})),
        paths=_dict_to_dataclass(PathsConfig, raw.get("paths", {})),
        excluded_article_types=raw.get("excluded_article_types", []),
        allowed_article_types=raw.get("allowed_article_types", []),
    )

    # Override NCBI settings with environment variables if present
    if os.environ.get("NCBI_API_KEY"):
        cfg.ncbi.api_key = os.environ["NCBI_API_KEY"]
    if os.environ.get("NCBI_EMAIL"):
        cfg.ncbi.email = os.environ["NCBI_EMAIL"]

    # If an API key is provided, bump the rate limit to 10 if it is at default (3)
    if cfg.ncbi.api_key and cfg.ncbi.rate_limit == 3:
        cfg.ncbi.rate_limit = 10

    # Resolve relative paths against the config-file's parent directory.
    cfg.paths._resolve(config_path.parent)
    return cfg
