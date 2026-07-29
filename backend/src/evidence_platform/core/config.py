import os
from pathlib import Path
from dataclasses import dataclass

# Base directory of the backend project
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Helper to load .env file manually into os.environ
def load_dotenv(dotenv_path: Path) -> None:
    if dotenv_path.exists():
        with open(dotenv_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key, val = parts[0].strip(), parts[1].strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    if key not in os.environ:
                        os.environ[key] = val

load_dotenv(BACKEND_DIR / ".env")

@dataclass
class Settings:
    DATABASE_URL: str = (
        f"sqlite:///{(BACKEND_DIR / os.environ.get('DATABASE_URL', 'sqlite:///evidence_platform.db').replace('sqlite:///', '')).resolve().as_posix()}"
        if os.environ.get("DATABASE_URL", "sqlite:///evidence_platform.db").startswith("sqlite:///") and not Path(os.environ.get("DATABASE_URL", "sqlite:///evidence_platform.db").replace("sqlite:///", "")).is_absolute()
        else os.environ.get("DATABASE_URL", "sqlite:///evidence_platform.db")
    )
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    QDRANT_HOST: str = os.environ.get("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.environ.get("QDRANT_PORT", "6333"))
    QDRANT_API_KEY: str = os.environ.get("QDRANT_API_KEY", "")
    
    NCBI_API_KEY: str = os.environ.get("NCBI_API_KEY", "")
    NCBI_EMAIL: str = os.environ.get("NCBI_EMAIL", "")
    
    LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "mock")
    LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
    LLM_MODEL_NAME: str = os.environ.get("LLM_MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")
    EMBEDDING_PROVIDER: str = os.environ.get("EMBEDDING_PROVIDER", "mock")
    RERANKER_PROVIDER: str = os.environ.get("RERANKER_PROVIDER", "mock")
    ENTAILMENT_PROVIDER: str = os.environ.get("ENTAILMENT_PROVIDER", "mock")
    
    SESSION_SECRET_KEY: str = os.environ.get("SESSION_SECRET_KEY", "local_development_secret_key_32_chars_long")
    JWT_SECRET_KEY: str = os.environ.get("JWT_SECRET_KEY", "local_development_jwt_secret_key")
    CSRF_SECRET_KEY: str = os.environ.get("CSRF_SECRET_KEY", "local_development_csrf_secret_key")
    
    # Path helper for source XML documents
    xml_dir: Path = BACKEND_DIR.parent / "pmc-pipeline" / "data" / "xml"
    json_dir: Path = BACKEND_DIR.parent / "pmc-pipeline" / "data" / "json"

settings = Settings()
