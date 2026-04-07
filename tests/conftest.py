"""
Root test configuration.

Patches src.config paths to a temporary directory so tests never
touch the real save/ folder.  Fixture YAML files from tests/fixtures/
are copied into the temp tree before each test session.
"""

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """
    Redirect every path in src.config to a disposable tmp_path tree.

    Layout inside tmp_path:
        save/
        save/connectors.yaml   (if fixture exists)
        save/pipelines/        (populated from fixtures/pipelines/)
    """
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    (save_dir / "pipelines").mkdir()

    # Copy fixture files when they exist
    for yaml_file in FIXTURES_DIR.glob("*.yaml"):
        shutil.copy(yaml_file, save_dir / yaml_file.name)

    # Ensure connectors.yaml always exists (load_manager expects it).
    # If no fixture provided one, create an empty file.
    connector_file = save_dir / "connectors.yaml"
    if not connector_file.exists():
        connector_file.write_text("{}\n")

    pipelines_fixture = FIXTURES_DIR / "pipelines"
    if pipelines_fixture.is_dir():
        for f in pipelines_fixture.glob("*.yaml"):
            shutil.copy(f, save_dir / "pipelines" / f.name)

    # Patch the module-level constants so every import sees temp paths
    import src.config as cfg

    monkeypatch.setattr(cfg, "SAVE_FOLDER", save_dir)
    monkeypatch.setattr(cfg, "CONNECTOR_FILE", save_dir / "connectors.yaml")
    monkeypatch.setattr(cfg, "PIPELINE_FOLDER", save_dir / "pipelines")