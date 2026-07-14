import argparse
import csv
import os
import sys


def dir_size(path: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for fname in filenames:
            fp = os.path.join(dirpath, fname)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def main():
    parser = argparse.ArgumentParser(
        description="Update the size column in a BIA file_list.tsv from zarr directories on disk."
    )
    parser.add_argument("file_list", help="Path to file_list.tsv")
    parser.add_argument(
        "--base-dir",
        "-b",
        default=".",
        help="Base directory containing the zarr directories (default: .)",
    )
    args = parser.parse_args()

    rows = []
    with open(args.file_list, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames
        for row in reader:
            zarr_path = os.path.join(args.base_dir, row["path"])
            if os.path.isdir(zarr_path):
                row["size"] = str(dir_size(zarr_path))
            else:
                print(f"Warning: not found: {zarr_path}", file=sys.stderr)
            rows.append(row)

    with open(args.file_list, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {len(rows)} rows in {args.file_list}")


if __name__ == "__main__":
    main()
