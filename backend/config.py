import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base de datos local
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./data/installed_base.db"
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
