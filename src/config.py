from pathlib import Path


SAVE_FOLDER = Path("save") # TODO replace with xdg and shit
CONNECTOR_FILE = SAVE_FOLDER / "connectors.yaml"
PIPELINE_FOLDER = SAVE_FOLDER / "pipelines"
SCRIPTS_FOLDER = SAVE_FOLDER / "scripts"
DB_FILE = SAVE_FOLDER / "pipechecker.db"

ALLOWED_SCRIPT_EXTENSIONS = {".sh", ".py"}
