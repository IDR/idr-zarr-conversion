#!/bin/bash

max_workers=14
input_file=
bf2raw="${BF2RAW:-bioformats2raw}"

chunk_w=128
chunk_h=128
chunk_z=128
sharding=1

shard_w=$((chunk_w * sharding))
shard_h=$((chunk_h * sharding))
shard_z=$((chunk_z * sharding))

while [[ $# -gt 0 ]]; do
    case $1 in
        --workers) max_workers="$2"; shift 2 ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *)  input_file="$1";         shift ;;
    esac
done

if [[ -z "$input_file" ]]; then
    echo "Error: no input file provided" >&2
    echo "" >&2
    echo "Usage: $(basename "$0") [--workers N] <input_file>" >&2
    echo "" >&2
    echo "  <input_file>  filepaths.tsv as written by metadata.py" >&2
    echo "  --workers N   Number of max workers (default: 14)" >&2
    exit 1
fi

if [[ ! -f "$input_file" ]]; then
    echo "Error: input file '$input_file' not found" >&2
    exit 1
fi

total=$(wc -l < "$input_file")
count=0
failed_lines=()
failed_lines_content=()

while IFS=$'\t' read -r target_dir filepath zarr_name extra; do
    count=$((count + 1))
    line_content=$(printf '%s\t%s\t%s' "$target_dir" "$filepath" "$zarr_name")
    [[ -n "$extra" ]] && line_content+=$(printf '\t%s' "$extra")
    if [[ -z "$zarr_name" ]]; then
        filename="${filepath##*/}"
        zarr_name="${filename%.*}.ome.zarr"
    fi
    target_dir="${target_dir#*Dataset:name:}"
    mkdir -p "/data/output/${target_dir}"
    echo "[$count/$total] Converting $filepath to ${target_dir}/${zarr_name}"
    command=("$bf2raw" --ngff-version=0.5 --downsample-type=AREA -c zstd -w "$chunk_w" -h "$chunk_h" -z "$chunk_z" --shard-width="$shard_w" --shard-height="$shard_h" --shard-depth="$shard_z" --max_workers="$max_workers" --memo-directory=/data/memo "$filepath" "/data/output/${target_dir}/${zarr_name}")
    printf 'Command:'
    printf ' %q' "${command[@]}"
    printf '\n'
    if ! "${command[@]}"; then
        failed_lines+=("$count")
        failed_lines_content+=("$line_content")
        echo "[$count/$total] FAILED: $filepath" >&2
    fi
done < "$input_file"

if [[ ${#failed_lines[@]} -gt 0 ]]; then
    echo "" >&2
    echo "Conversion finished with ${#failed_lines[@]} error(s):" >&2
    for i in "${!failed_lines[@]}"; do
        echo "  Line ${failed_lines[$i]}: ${failed_lines_content[$i]}" >&2
    done
    exit 1
fi

echo "Conversion completed successfully ($total/$total)."
