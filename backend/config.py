import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")

# Base de datos local
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///" + (BACKEND_DIR / "data" / "installed_base.db").as_posix()
)

# ----------------------------------------------------------------------
# QVAC — inferencia en el dispositivo
# ----------------------------------------------------------------------
# Modelo pequeño y rápido para el prototipo. QWEN3_600M_INST_Q4 ocupa
# alrededor de 382 MB según el catálogo de QVAC y es adecuado para una
# extracción estructurada ligera.
QVAC_MODEL = os.getenv(
    "QVAC_MODEL",
    "QWEN3_600M_INST_Q4"
)

QVAC_CTX_SIZE = int(
    os.getenv("QVAC_CTX_SIZE", "4096")
)

# Directorio local donde QVAC conserva modelos/cache.
_default_cache = Path(__file__).resolve().parent / "data" / "qvac-cache"
QVAC_CACHE_DIR = os.getenv(
    "QVAC_CACHE_DIR",
    str(_default_cache)
)

# Si QVAC no está listo, el backend NO debe enviar datos a ninguna nube.
# En ese caso devuelve 503 y muestra claramente que la inferencia local
# no está disponible.
QVAC_REQUIRED = os.getenv(
    "QVAC_REQUIRED",
    "true"
).strip().lower() in {"1", "true", "yes", "on"}

# Relative paths have the same meaning whether started from root or backend/.
QVAC_CACHE_DIR = str((BACKEND_DIR / QVAC_CACHE_DIR).resolve())
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL[len("sqlite:///"):]
    if db_path != ":memory:" and not Path(db_path).is_absolute():
        DATABASE_URL = "sqlite:///" + (BACKEND_DIR / db_path).resolve().as_posix()

QVAC_ASR_MODEL = os.getenv("QVAC_ASR_MODEL", "WHISPER_TINY")
QVAC_INFERENCE_TIMEOUT_SECONDS = float(os.getenv("QVAC_INFERENCE_TIMEOUT_SECONDS", "120"))
QVAC_STARTUP_TIMEOUT_SECONDS = float(os.getenv("QVAC_STARTUP_TIMEOUT_SECONDS", "240"))
os.environ.setdefault("QVAC_RPC_INIT_TIMEOUT_MS", "120000")
QVAC_ENABLED = os.getenv("QVAC_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
MAX_FOLLOW_UP_ROUNDS = 2
MAX_CAPTURE_ROUNDS = 6
MAX_TEXT_LENGTH = 4000
MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_AUDIO_SECONDS = 60
FRESH_DAYS = 180
STALE_DAYS = 365
REFRESH_OPPORTUNITY_YEARS = 7
CORS_ORIGINS = ["http://127.0.0.1:5500", "http://localhost:5500"]
