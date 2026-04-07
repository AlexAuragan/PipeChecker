from pathlib import Path


SAVE_FOLDER = Path("save") # TODO replace with xdg and shit
CONNECTOR_FILE = SAVE_FOLDER / "connectors.yaml"
PIPELINE_FOLDER = SAVE_FOLDER / "pipelines"
DB_FILE = SAVE_FOLDER / "pipechecker.db"