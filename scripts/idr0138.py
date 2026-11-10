#!/usr/bin/env python3
"""Zip pre-existing OME-Zarr stores into the BIA output layout.

Special case for studies (e.g. idr0138) whose files are already OME-Zarr:
``convert.sh``/bioformats2raw are not needed — the ``.ome.zarr`` directories
just have to be zipped into the study output structure.

Input is the IDR-provided ``*-filePaths.tsv`` (three columns, no header):

    Dataset:name:<dataset>\t/uod/idr/.../<x>.ome.zarr\t<x>.ome.zarr

Each source ``.ome.zarr`` directory is zipped (stored, ``zip -0``) to

    <output_dir>/<component>/<dataset>/<x>.ome.zarr.zip

The component (e.g. ``experimentA``) is derived from the input filename
(``idr0138-experimentA-filePaths.tsv``) or set with ``--component``.

After zipping, ``file_list.tsv`` (in <output_dir>, or ``--file-list``) is
updated in place: rows whose ``file_path`` was a zipped source path are
rewritten to the relative zip path and their ``size_in_bytes`` filled in;
other non-URL rows (e.g. ``.log`` files) are dropped. URL rows are kept.
A ``file_list.tsv.bak`` backup is written alongside.

Usage:
    idr0138.py [--workers N] [--component experimentA] <input_file> [<output_dir>]
"""

import argparse
import csv
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def derive_component(input_file: str) -> str:
    m = re.search(r"(experiment|screen)[A-Z]+", Path(input_file).stem)
    return m.group(0) if m else "experimentA"


def zip_row(row):
    lineno, target_dir, source, zarr_name, out_root = row
    source_path = Path(source)
    if not zarr_name:
        zarr_name = source_path.name
    if not source_path.is_dir():
        return lineno, target_dir, source, zarr_name, "source dir not found"
    out_dir = out_root / target_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{zarr_name}.zip"
    if zip_path.exists():
        return None
    result = subprocess.run(
        ["zip", "-1", "-r", str(zip_path), source_path.name],
        cwd=source_path.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        zip_path.unlink(missing_ok=True)
        return lineno, target_dir, source, zarr_name, result.stderr.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zip existing .ome.zarr directories (from an IDR "
        "filePaths.tsv) into the study output structure."
    )
    parser.add_argument("input_file", help="IDR filePaths.tsv")
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="/data/output/idr0138-lohoff-seqfish",
        help="Study output root (default: %(default)s)",
    )
    parser.add_argument(
        "--component",
        help="Component subdir, e.g. experimentA (default: derived from "
        "the input filename)",
    )
    parser.add_argument("--workers", type=int, default=14)
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip zipping; only update file_list.tsv",
    )
    parser.add_argument(
        "--file-list",
        help="file_list.tsv to update (default: <output_dir>/file_list.tsv)",
    )
    args = parser.parse_args()

    input_file = Path(args.input_file)
    if not input_file.is_file():
        print(f"Error: input file '{input_file}' not found", file=sys.stderr)
        return 1

    component = args.component or derive_component(args.input_file)
    out_root = Path(args.output_dir) / component

    rows = []
    for lineno, line in enumerate(
        input_file.open(encoding="utf-8"), 1
    ):
        cols = line.rstrip("\n").split("\t")
        if len(cols) < 2 or not cols[1].strip():
            continue
        target_dir = cols[0].split("Dataset:name:")[-1]
        target_dir = target_dir.split("Screen:name:")[-1]
        zarr_name = cols[2].strip() if len(cols) > 2 else ""
        rows.append(
            (lineno, target_dir, cols[1].strip(), zarr_name, out_root)
        )

    if args.no_zip:
        failures = []
    else:
        print(f"Zipping {len(rows)} OME-Zarr store(s) to {out_root}/...")
        failures = []
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(zip_row, row) for row in rows]
            for future in as_completed(futures):
                result = future.result()
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(rows)}")
                if result:
                    failures.append(result)

    if failures:
        print(
            f"\nZipping finished with {len(failures)} error(s):",
            file=sys.stderr,
        )
        for lineno, target_dir, source, zarr_name, err in failures:
            print(
                f"  Line {lineno}: {target_dir}\t{source}\t{zarr_name}\t{err}",
                file=sys.stderr,
            )
        return 1

    if not args.no_zip:
        print(f"Zipping completed successfully ({len(rows)}/{len(rows)}).")

    file_list = Path(args.file_list) if args.file_list else (
        Path(args.output_dir) / "file_list.tsv"
    )
    if file_list.is_file():
        # Map source .ome.zarr path -> relative zip path under output_dir,
        # indexed by basename for suffix matching (the paths in
        # file_list.tsv may differ in leading components, e.g. a missing
        # leading slash).
        source_to_zip = {}
        by_basename = {}
        for _, target_dir, source, zarr_name, _ in rows:
            source_path = Path(source)
            if not zarr_name:
                zarr_name = source_path.name
            rel = f"{component}/{target_dir}/{zarr_name}.zip"
            zip_file = Path(args.output_dir) / rel
            key = source_path.as_posix().lstrip("/")
            source_to_zip[key] = (rel, zip_file)
            by_basename.setdefault(source_path.name, []).append(key)

        def find_match(fp: str):
            """Match a file_path to a source path by suffix equality."""
            norm = fp.lstrip("/")
            if norm in source_to_zip:
                return source_to_zip[norm]
            for key in by_basename.get(Path(norm).name, []):
                if norm.endswith(key) or key.endswith(norm):
                    return source_to_zip[key]
            return None

        with file_list.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            fieldnames = reader.fieldnames
            file_rows = list(reader)

        kept = []
        unmatched = []
        for row in file_rows:
            fp = row.get("file_path", "")
            if re.match(r"^[a-z][a-z0-9+.-]*://", fp, re.I):
                kept.append(row)
                continue
            match = find_match(fp)
            if match is None:
                # Local non-zarr rows (e.g. .log files) don't apply here.
                unmatched.append(fp)
                continue
            rel, zip_file = match
            row["file_path"] = rel
            if zip_file.is_file():
                row["size_in_bytes"] = str(zip_file.stat().st_size)
            kept.append(row)

        if not any(r["file_path"].endswith(".zip") for r in kept) and file_rows:
            print(
                "ERROR: no file_list.tsv rows matched any source path; "
                "not writing changes.",
                file=sys.stderr,
            )
            for fp in unmatched[:5]:
                print(f"  unmatched: {fp}", file=sys.stderr)
            for key in list(source_to_zip)[:5]:
                print(f"  source:    /{key}", file=sys.stderr)
            return 1

        shutil.copy2(file_list, file_list.with_suffix(".tsv.bak"))
        with file_list.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, delimiter="\t"
            )
            writer.writeheader()
            writer.writerows(kept)
        print(
            f"Updated {file_list}: {len(kept)} rows kept "
            f"(removed {len(file_rows) - len(kept)})."
        )
    else:
        print(f"Note: no file_list.tsv at {file_list}; skipped update.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
