#!/usr/bin/env python3
"""Sanity-check a converted study output directory.

Cross-checks three sources:

- ``filepaths.tsv`` (written by ``metadata.py``, consumed by ``convert.sh``):
  every row should have produced a ``<name>.ome.zarr.zip`` (larger than
  ``--min-zip-kb``) and a non-empty ``<name>.ome.zarr.log``.
- ``file_list.tsv`` (the BIA RO-Crate file list): every non-URL ``file_path``
  should exist under the study directory. Missing files (i.e. failed
  conversions) can optionally be removed from ``file_list.tsv``. Every
  non-URL row should also have a real ``size_in_bytes`` (run
  ``update_sizes.py`` to fill them in).
- ``ro-crate-metadata.json``: should parse, contain a ``bia:Study`` root
  entity, a ``file_list.tsv`` entity, and no dangling ``@id`` references.
  If the ``bia-ro-crate`` CLI is installed, its ``validate`` command is
  run too.
- Files on disk: any regular file not listed in ``file_list.tsv`` (and not a
  crate bookkeeping file) is flagged and can optionally be deleted, as can
  leftover unzipped ``.ome.zarr`` directories.

Usage:
    check_study.py <study_dir> [--min-zip-kb 100] [--yes] [--dry-run]
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BOOKKEEPING_FILES = {
    "filepaths.tsv",
    "file_list.tsv",
    "file_list.tsv.bak",
    "ro-crate-metadata.json",
}

URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def expected_output_dir(study_dir: Path, target_dir: str) -> Path:
    """Map a filepaths.tsv column-1 value to a directory under study_dir.

    Column 1 is rooted at the study accession (e.g.
    ``idr0091-.../experimentA/<dataset>``) and may contain a
    ``Dataset:name:`` infix which convert.sh strips.
    """
    target_dir = target_dir.split("Dataset:name:")[-1]
    parts = Path(target_dir).parts
    candidates = []
    if len(parts) > 1:
        candidates.append(study_dir.joinpath(*parts[1:]))
    candidates.append(study_dir / target_dir)
    for candidate in candidates:
        if candidate.parent.is_dir() or candidate.is_dir():
            return candidate
    return candidates[0]


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"{prompt} yes (--yes)")
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def check_filepaths(study_dir: Path, filepaths: Path, min_zip_bytes: int) -> int:
    """Check expected zips and logs for every filepaths.tsv row."""
    problems = 0
    total = 0
    with filepaths.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            total += 1
            cols = line.split("\t")
            target_dir = cols[0]
            zarr_name = cols[2] if len(cols) > 2 else ""
            if not zarr_name:
                src = cols[1] if len(cols) > 1 else ""
                zarr_name = Path(src).stem + ".ome.zarr" if src else ""
            if not zarr_name:
                print(f"  line {lineno}: cannot determine zarr name: {line}")
                problems += 1
                continue
            out_dir = expected_output_dir(study_dir, target_dir)
            zip_path = out_dir / f"{zarr_name}.zip"
            log_path = out_dir / f"{zarr_name}.log"

            if not zip_path.is_file():
                if (out_dir / zarr_name).is_dir():
                    print(f"  NOT ZIPPED: {out_dir / zarr_name} (dir exists, no .zip)")
                else:
                    print(f"  MISSING zip: {zip_path}")
                problems += 1
            elif zip_path.stat().st_size < min_zip_bytes:
                print(
                    f"  SMALL zip ({zip_path.stat().st_size} bytes "
                    f"< {min_zip_bytes}): {zip_path}"
                )
                problems += 1

            if not log_path.is_file():
                print(f"  MISSING log: {log_path}")
                problems += 1
            elif log_path.stat().st_size == 0:
                print(f"  EMPTY log: {log_path}")
                problems += 1

    print(f"filepaths.tsv: {total} rows, {problems} problem(s)\n")
    return problems


def load_file_list(file_list: Path) -> tuple[list, list]:
    with file_list.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return reader.fieldnames, list(reader)


def write_file_list(file_list: Path, fieldnames: list, rows: list) -> None:
    backup = file_list.with_suffix(file_list.suffix + ".bak")
    shutil.copy2(file_list, backup)
    with file_list.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {file_list} (backup: {backup.name})")


def check_file_list(
    study_dir: Path,
    file_list: Path,
    rows: list,
    assume_yes: bool,
    dry_run: bool,
) -> int:
    """Check every non-URL file_list.tsv entry exists; offer to drop stale rows."""
    missing = []
    for row in rows:
        path_str = row.get("file_path", "")
        if not path_str or URL_RE.match(path_str):
            continue
        if not (study_dir / path_str).exists():
            missing.append(path_str)
            print(f"  missing: {path_str}")

    print(f"file_list.tsv: {len(rows)} rows, {len(missing)} missing on disk\n")
    if not missing:
        return 0

    if dry_run:
        print("  --dry-run: file_list.tsv left unchanged\n")
        return len(missing)

    if confirm(f"Remove {len(missing)} stale row(s) from file_list.tsv?", assume_yes):
        missing_set = set(missing)
        kept = [r for r in rows if r.get("file_path") not in missing_set]
        fieldnames, _ = load_file_list(file_list)
        write_file_list(file_list, fieldnames, kept)
        return 0
    return len(missing)


def check_file_list_sizes(rows: list) -> int:
    """Flag non-URL file_list.tsv rows without a real size_in_bytes."""
    bad = []
    for row in rows:
        path_str = row.get("file_path", "")
        if not path_str or URL_RE.match(path_str):
            continue
        size = row.get("size_in_bytes", "")
        try:
            if int(size) > 0:
                continue
        except (TypeError, ValueError):
            pass
        bad.append(path_str)
    for p in bad[:10]:
        print(f"  no size: {p}")
    if len(bad) > 10:
        print(f"  ... and {len(bad) - 10} more")
    print(f"file_list.tsv sizes: {len(bad)} row(s) missing size_in_bytes\n")
    if bad:
        print("  hint: run 'update_sizes.py <file_list.tsv>' to fill them in\n")
    return len(bad)


def _walk_refs(value):
    """Yield every @id reference found in a nested structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "@id" and isinstance(item, str):
                yield item
            else:
                yield from _walk_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_refs(item)


def check_ro_crate(study_dir: Path) -> int:
    """Validate ro-crate-metadata.json structurally (and via bia-ro-crate)."""
    problems = 0
    crate_path = study_dir / "ro-crate-metadata.json"
    if not crate_path.is_file():
        print(f"  missing: {crate_path}")
        return 1
    try:
        crate = json.loads(crate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  invalid JSON: {e}")
        return 1

    graph = crate.get("@graph")
    if "@context" not in crate:
        print("  missing @context")
        problems += 1
    if not isinstance(graph, list):
        print("  missing @graph")
        return problems + 1

    ids = {
        e["@id"] for e in graph if isinstance(e, dict) and isinstance(e.get("@id"), str)
    }
    root = next((e for e in graph if isinstance(e, dict) and e.get("@id") == "./"), None)
    if root is None:
        print("  no root entity ('@id': './')")
        problems += 1
    else:
        root_type = root.get("@type") or []
        if not isinstance(root_type, list):
            root_type = [root_type]
        if "bia:Study" not in root_type:
            print(f"  root entity is not a bia:Study (@type={root_type})")
            problems += 1
        if not root.get("accessionId"):
            print("  root entity has no accessionId")
            problems += 1
    if "file_list.tsv" not in ids:
        print("  no file_list.tsv entity in @graph")
        problems += 1

    for entity in graph:
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("@id", "?")
        for key, value in entity.items():
            if key in ("@id", "@type", "@context"):
                continue
            for ref in _walk_refs(value):
                if ref not in ids and not URL_RE.match(ref):
                    print(f"  dangling ref: {entity_id} -> {ref} ({key})")
                    problems += 1

    print(f"ro-crate-metadata.json: {problems} problem(s)")

    bia_cli = shutil.which("bia-ro-crate")
    if bia_cli:
        result = subprocess.run(
            [bia_cli, "validate", str(study_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("  bia-ro-crate validate FAILED:")
            print("  " + "\n  ".join((result.stdout + result.stderr).splitlines()[:20]))
            problems += 1
        else:
            print("  bia-ro-crate validate: OK")
    else:
        print("  bia-ro-crate not installed; external validation skipped")
    print()
    return problems


def check_unlisted_files(
    study_dir: Path,
    listed_paths: set,
    assume_yes: bool,
    dry_run: bool,
) -> int:
    """Flag files on disk not referenced by file_list.tsv; offer to delete."""
    extra_files = []
    extra_zarr_dirs = []
    for path in sorted(study_dir.rglob("*")):
        rel = path.relative_to(study_dir).as_posix()
        if path.is_dir():
            if path.name.endswith(".ome.zarr"):
                extra_zarr_dirs.append(path)
            continue
        if rel in BOOKKEEPING_FILES or rel in listed_paths:
            continue
        # Files inside a leftover .ome.zarr dir are reported via the dir.
        if any(p.name.endswith(".ome.zarr") for p in path.parents):
            continue
        extra_files.append(path)

    for p in extra_files:
        print(f"  unlisted file: {p.relative_to(study_dir)}")
    for p in extra_zarr_dirs:
        print(f"  leftover unzipped dir: {p.relative_to(study_dir)}/")
    print(
        f"unlisted: {len(extra_files)} file(s), "
        f"{len(extra_zarr_dirs)} unzipped .ome.zarr dir(s)\n"
    )

    problems = len(extra_files) + len(extra_zarr_dirs)
    if not problems or dry_run:
        if problems and dry_run:
            print("  --dry-run: nothing deleted\n")
        return problems

    remaining = problems
    if extra_files and confirm(
        f"Delete {len(extra_files)} unlisted file(s)?", assume_yes
    ):
        for p in extra_files:
            p.unlink()
        remaining -= len(extra_files)
    if extra_zarr_dirs and confirm(
        f"Delete {len(extra_zarr_dirs)} unzipped .ome.zarr dir(s)?", assume_yes
    ):
        for p in extra_zarr_dirs:
            shutil.rmtree(p)
        remaining -= len(extra_zarr_dirs)
    return remaining


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanity-check a converted study directory against "
        "filepaths.tsv and file_list.tsv."
    )
    parser.add_argument("study_dir", type=Path, help="Study output directory")
    parser.add_argument(
        "--min-zip-kb",
        type=int,
        default=100,
        help="Minimum expected .ome.zarr.zip size in KB (default: 100)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Auto-accept all prompts"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only; do not edit file_list.tsv or delete anything",
    )
    args = parser.parse_args()

    study_dir = args.study_dir
    filepaths = study_dir / "filepaths.tsv"
    file_list = study_dir / "file_list.tsv"
    for required in (filepaths, file_list):
        if not required.is_file():
            print(f"Error: {required} not found", file=sys.stderr)
            return 2

    problems = 0

    print("== Checking expected outputs from filepaths.tsv ==")
    problems += check_filepaths(study_dir, filepaths, args.min_zip_kb * 1024)

    print("== Checking file_list.tsv entries exist ==")
    fieldnames, rows = load_file_list(file_list)
    problems += check_file_list(
        study_dir, file_list, rows, args.yes, args.dry_run
    )

    print("== Checking file_list.tsv sizes ==")
    problems += check_file_list_sizes(rows)

    print("== Checking ro-crate-metadata.json ==")
    problems += check_ro_crate(study_dir)

    print("== Checking for files not listed in file_list.tsv ==")
    listed = {
        r["file_path"]
        for r in rows
        if r.get("file_path") and not URL_RE.match(r["file_path"])
    }
    problems += check_unlisted_files(
        study_dir, listed, args.yes, args.dry_run
    )

    if problems:
        print(f"FAILED: {problems} unresolved problem(s)")
        return 1
    print("OK: study directory is consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
