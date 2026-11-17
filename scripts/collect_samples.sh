#!/bin/bash
# Collect one OME-Zarr sample per immediate idr* study directory.
# Select the alphabetically first .ome.zarr.zip recursively in each study,
# show its path, ZIP size and destination free space, then ask before extracting.
# Extract into <output_dir>/<study>/, preserving the archive's internal directory;
# skip existing sample directories without overwriting them. Enter skips; q quits.
# Usage: collect_samples.sh <input_dir> <output_dir>
# Example: collect_samples.sh /data/output /data/samples

set -u
export LC_ALL=C
shopt -s nullglob dotglob

if [[ $# -ne 2 ]]; then
    echo "Usage: $(basename "$0") <input_dir> <output_dir>" >&2
    exit 1
fi
if [[ ! -d "$1" ]]; then
    echo "Error: input directory '$1' not found" >&2
    exit 1
fi
for tool in unzip df wc awk; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Error: required tool '$tool' not found" >&2
        exit 1
    fi
done
input_dir=$(cd "$1" && pwd -P) || exit 1
mkdir -p "$2" || exit 1
output_dir=$(cd "$2" && pwd -P) || exit 1

select_archive() {
    local path
    for path in "$1"/*; do
        if [[ -d "$path" && ! -L "$path" ]]; then
            select_archive "$path"
        elif [[ -f "$path" && "$path" == *.ome.zarr.zip ]]; then
            if [[ -z "$archive" || "$path" < "$archive" ]]; then
                archive="$path"
            fi
        fi
    done
}

failed=0
for study_dir in "$input_dir"/idr*/; do
    [[ -d "$study_dir" ]] || continue
    study_dir="${study_dir%/}"
    study="${study_dir##*/}"
    archive=
    select_archive "$study_dir"
    if [[ -z "$archive" ]]; then
        echo "Skip $study: no .ome.zarr.zip found"
        continue
    fi
    destination="$output_dir/$study"
    sample="${archive##*/}"
    sample="${sample%.zip}"
    if [[ -e "$destination/$sample" || -L "$destination/$sample" ]]; then
        echo "Skip $study: '$destination/$sample' already exists"
        continue
    fi
    bytes=$(wc -c < "$archive") || { failed=1; continue; }
    size=$(awk -v bytes="$bytes" 'BEGIN {
        split("B KiB MiB GiB TiB PiB", units, " "); i=1;
        while (bytes>=1024 && i<6) { bytes/=1024; i++ }
        printf "%.1f %s", bytes, units[i]
    }')
    printf '\nZIP: %s\nZIP size: %s (%s bytes)\nDestination: %s/\n' "$archive" "$size" "$bytes" "$destination"
    echo "Destination disk space (Avail column):"
    df -h "$output_dir" || { failed=1; continue; }
    echo "Note: extracted contents may be much larger than the ZIP."
    printf 'Extract? [y/N/q] '
    if ! IFS= read -r answer; then
        printf '\n'
        break
    fi
    case "$answer" in
        q|Q|quit|QUIT) break ;;
        y|Y|yes|YES|Yes)
            if mkdir -p "$destination" && unzip -n -q "$archive" -d "$destination"; then
                echo "Extracted: $destination/$sample"
            else
                echo "Error: extraction failed for '$archive'; partial output may remain" >&2
                failed=1
            fi
            ;;
        *) echo "Skipped: $study" ;;
    esac
done
exit "$failed"
