from pathlib import Path
import os


ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
LORAS_DIR = ROOT / "loras"
EMBEDDINGS_DIR = ROOT / "embeddings"
CONTROL_ASSETS_DIR = ROOT / "control_assets"
ARTIFACTS_DIR = ROOT / "artifacts"
RUNTIME_DIR = ROOT / "runtime"
LOGS_DIR = RUNTIME_DIR / "logs"
ASSET_REQUESTS_DIR = ROOT / "asset_requests"
DB_PATH = RUNTIME_DIR / "forge.sqlite3"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8110
CPU_THREADS = os.cpu_count() or 32
MODEL_IDLE_SECONDS = int(os.environ.get("FORGE_MODEL_IDLE_SECONDS", "1800"))
EMBEDDED_WORKER = os.environ.get("FORGE_EMBEDDED_WORKER", "1") not in {"0", "false", "False"}

MEMORY_ENABLED = os.environ.get("FORGE_MEMORY_ENABLED", "1") not in {"0", "false", "False", "no", "off"}
MEMORY_NAMESPACE = os.environ.get("FORGE_MEMORY_NAMESPACE", "demonsforge").strip() or "demonsforge"
MEMORY_REQUESTER = os.environ.get("FORGE_MEMORY_REQUESTER", "demonsforge").strip() or "demonsforge"
ARCHIVE_BASE_URL = os.environ.get("FORGE_ARCHIVE_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
ARCHIVE_API_KEY = os.environ.get("FORGE_ARCHIVE_API_KEY", os.environ.get("ARCHIVE_API_KEY", "")).strip()
MEMORY_READ_TIMEOUT_SECONDS = float(os.environ.get("FORGE_MEMORY_READ_TIMEOUT_SECONDS", os.environ.get("FORGE_MEMORY_TIMEOUT_SECONDS", "5")))
MEMORY_WRITE_TIMEOUT_SECONDS = float(os.environ.get("FORGE_MEMORY_WRITE_TIMEOUT_SECONDS", os.environ.get("FORGE_MEMORY_TIMEOUT_SECONDS", "30")))
MEMORY_PROPOSAL_MAX_CHARS = int(os.environ.get("FORGE_MEMORY_PROPOSAL_MAX_CHARS", "4000"))
MEMORY_EVIDENCE_MAX_CHARS = int(os.environ.get("FORGE_MEMORY_EVIDENCE_MAX_CHARS", "3000"))

MAX_WIDTH = 1536
MAX_HEIGHT = 1536
MAX_STEPS = 60
MAX_BATCH = 4
MAX_ASSET_DOWNLOAD_BYTES = int(os.environ.get("FORGE_MAX_ASSET_DOWNLOAD_BYTES", str(80 * 1024**3)))


def ensure_dirs() -> None:
    for path in [
        MODELS_DIR,
        LORAS_DIR,
        EMBEDDINGS_DIR,
        CONTROL_ASSETS_DIR,
        ARTIFACTS_DIR,
        RUNTIME_DIR,
        LOGS_DIR,
        ASSET_REQUESTS_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def force_cpu_runtime() -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ.setdefault("OMP_NUM_THREADS", str(CPU_THREADS))
    os.environ.setdefault("MKL_NUM_THREADS", str(CPU_THREADS))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(CPU_THREADS))
    os.environ.setdefault("NUMEXPR_NUM_THREADS", str(CPU_THREADS))
    os.environ.setdefault("HF_HOME", str(ROOT / "hf_home"))
