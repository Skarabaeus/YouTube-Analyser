"""Find YouTube Studio export folders in the exports root."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

ExportKind = Literal["date", "video"]
ExportStatus = Literal["accepted", "rejected", "unsupported", "skipped"]

FOLDER_RE = re.compile(
    r"^(?P<dimension>\S+) (?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2}) "
    r"(?P<channel>.+?)(?:-(?P<copy>\d+))?$"
)
DIMENSION_KINDS: dict[str, ExportKind] = {
    "datum": "date",
    "date": "date",
    "videos": "video",
    "video": "video",
    "content": "video",
    "inhalte": "video",
}
TABLE_FILES = ("Tabellendaten.csv", "Table data.csv")


@dataclass
class ExportFolder:
    """One export folder and what ingestion made of it."""

    name: str
    path: Path | None
    status: ExportStatus
    reason: str = ""
    dimension: str = ""
    kind: ExportKind | None = None
    start: date | None = None
    end: date | None = None
    channel: str = ""
    mtime_ns: int = 0
    unmapped_headers: list[str] = field(default_factory=list)

    @property
    def export_id(self) -> str:
        return self.name

    def reject(self, reason: str) -> None:
        self.status = "rejected"
        self.reason = reason


def table_file(folder: Path) -> Path | None:
    for name in TABLE_FILES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def discover(root: Path) -> tuple[list[ExportFolder], str | None]:
    """Scan one level below ``root``. Returns the export folders and a root-level problem."""
    if not root.exists():
        return [], f"exports directory does not exist: {root}"
    if not root.is_dir():
        return [], f"exports path is not a directory: {root}"
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        return [], f"exports directory is not readable: {exc.strerror}"

    folders: list[ExportFolder] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            folders.append(
                ExportFolder(entry.name, None, "skipped", "symbolic links are not followed")
            )
            continue
        if not entry.is_dir():
            folders.append(ExportFolder(entry.name, None, "skipped", "not a folder"))
            continue
        folders.append(_inspect(entry))
    return folders, None


def _inspect(folder: Path) -> ExportFolder:
    match = FOLDER_RE.match(folder.name)
    if not match:
        return ExportFolder(
            folder.name,
            folder,
            "skipped",
            "name does not match '<Dimension> <YYYY-MM-DD>_<YYYY-MM-DD> <Channel>'",
        )
    try:
        start = date.fromisoformat(match["start"])
        end = date.fromisoformat(match["end"])
    except ValueError:
        return ExportFolder(folder.name, folder, "skipped", "invalid date in folder name")
    if end < start:
        return ExportFolder(folder.name, folder, "skipped", "period end is before period start")

    dimension = match["dimension"]
    export = ExportFolder(
        folder.name,
        folder,
        "accepted",
        dimension=dimension,
        kind=DIMENSION_KINDS.get(dimension.casefold()),
        start=start,
        end=end,
        channel=match["channel"].strip(),
    )
    if export.kind is None:
        export.status = "unsupported"
        export.reason = f"dimension '{dimension}' is not supported (supported: Datum, Videos)"
        return export
    table = table_file(folder)
    if table is None:
        export.reject(f"no {' or '.join(TABLE_FILES)} found")
        return export
    export.mtime_ns = table.stat().st_mtime_ns
    return export
