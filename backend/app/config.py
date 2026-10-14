from pathlib import Path
import os
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")
DATA_DIR = Path(os.getenv("SIGMAPILOT_DATA_DIR", ROOT_DIR / "data"))
UPLOAD_DIR = Path(os.getenv("SIGMAPILOT_UPLOAD_DIR", ROOT_DIR / "storage" / "uploads"))
DB_PATH = Path(os.getenv("SIGMAPILOT_DB_PATH", DATA_DIR / "sigmapilot.sqlite3"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "15"))

# OpenAI-compatible API settings. NVIDIA NIM can be used by setting the base URL,
# model, and API key in .env. No key is required for deterministic/offline mode.
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "meta/llama-3.3-70b-instruct").strip()
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
