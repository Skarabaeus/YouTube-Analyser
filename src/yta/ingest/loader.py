"""Run discovery, parsing and validation for a whole exports root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yta.ingest.discovery import ExportFolder, discover
from yta.ingest.merge import MergedData, merge
from yta.ingest.tables import ExportRejected, ParsedExport, parse_export


@dataclass
class LoadResult:
    root: Path
    folders: list[ExportFolder]
    parsed: list[ParsedExport]
    merged: MergedData
    root_problem: str | None


def load(root: Path) -> LoadResult:
    folders, root_problem = discover(root)
    parsed: list[ParsedExport] = []
    for folder in folders:
        if folder.status != "accepted":
            continue
        try:
            parsed.append(parse_export(folder))
        except ExportRejected as exc:
            folder.reject(str(exc))
        except OSError as exc:
            folder.reject(f"could not read export: {exc.strerror}")
    return LoadResult(root, folders, parsed, merge(parsed), root_problem)
