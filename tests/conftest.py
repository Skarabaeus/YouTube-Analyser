import csv
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

DATUM_HEADER = [
    "Datum",
    "Aufrufe",
    "Thumbnail-Impressionen",
    "Thumbnail-Klickrate (%)",
    "Einzelne Zuschauer",
]


def write_export(
    root: Path, folder: str, header: list[str], rows: list[list], *, file: str = "Tabellendaten.csv"
) -> Path:
    path = root / folder / file
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def basic_root(tmp_path: Path) -> Path:
    """Writable copy of the basic fixture set."""
    target = tmp_path / "exports"
    shutil.copytree(FIXTURES / "basic", target)
    return target
