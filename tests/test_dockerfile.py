"""The API image must ship every data file the service reads at runtime."""
from __future__ import annotations

import shlex
from pathlib import Path

from app import hero_demo, risk_signals, shifted_external

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "services" / "api" / "Dockerfile"


def copied_data_files() -> set[Path]:
    files: set[Path] = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        parts = shlex.split(line)
        if not parts or parts[0].upper() != "COPY":
            continue
        for source in parts[1:-1]:
            if source.startswith("data/"):
                files.update(path.resolve() for path in ROOT.glob(source))
    return files


def test_api_image_copies_runtime_data():
    runtime = {
        hero_demo._FIXTURE, hero_demo._OPTIONS, hero_demo.HERO_WORKBOOK, risk_signals.CORPUS,
        *(shifted_external.ROOT / "data" / "external").glob("*-holidays.json"),
    }
    missing = {path.resolve() for path in runtime} - copied_data_files()
    assert not missing, f"Dockerfile does not COPY: {sorted(str(path.relative_to(ROOT)) for path in missing)}"


def test_api_image_excludes_ground_truth():
    assert not any("l3_ground_truth" in path.parts or "evaluation" in path.parts for path in copied_data_files())
