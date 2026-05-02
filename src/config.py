import os
from pathlib import Path

PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)  # src/config.py → src/ → project root
SAVE_FOLDER = PROJECT_ROOT / "save"  # TODO replace with xdg and shit
CONNECTOR_FILE = SAVE_FOLDER / "connectors.yaml"
PIPELINE_FOLDER = SAVE_FOLDER / "pipelines"
SCRIPTS_FOLDER = SAVE_FOLDER / "scripts"
DB_FILE = SAVE_FOLDER / "pipechecker.db"

ALLOWED_SCRIPT_EXTENSIONS = {".sh", ".py"}

BASE_URL = os.environ.get("PIPECHECKER_BASE_URL", "http://127.0.0.1:8000")

ALERT_FILE = SAVE_FOLDER / "alerts.yaml"
ALERT_CONNECTOR_FILE = SAVE_FOLDER / "alert_connectors.yaml"
