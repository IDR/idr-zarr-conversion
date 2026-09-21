import argparse
import csv
import sys
from pathlib import Path


def update_image_sizes(file_list: Path) -> int:
    base_dir = file_list.parent
    updated = 0
    rows = []

    with file_list.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames
        for row in reader:
            path = Path(row.get("file_path", ""))
            size = row.get("size_in_bytes", "")
            if size == "-1":
                full_path = base_dir / path
                if full_path.is_file():
                    row["size_in_bytes"] = str(full_path.stat().st_size)
                    updated += 1
                elif full_path.suffixes and full_path.suffixes[-1] == ".zarr":
                    zip_path = base_dir / f"{path}.zip"
                    if zip_path.is_file():
                        row["size_in_bytes"] = str(zip_path.stat().st_size)
                        updated += 1
                    else:
                        print(f"Warning: zip not found: {zip_path}", file=sys.stderr)
            rows.append(row)

    with file_list.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update size_in_bytes in a BIA file_list.tsv from zipped OME-Zarr files."
    )
    parser.add_argument("file_list", type=Path, help="Path to file_list.tsv")
    args = parser.parse_args()

    updated = update_image_sizes(args.file_list)
    print(f"Updated {updated} rows in {args.file_list}")


if __name__ == "__main__":
    main()
