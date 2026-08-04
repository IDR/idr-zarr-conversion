#!/bin/bash

max_workers=14
id=
input_file=

while [[ $# -gt 0 ]]; do
    case $1 in
        --workers) max_workers="$2"; shift 2 ;;
        --id)      id="$2";          shift 2 ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *)  input_file="$1";         shift ;;
    esac
done

if [[ -z "$input_file" ]]; then
    echo "Error: no input file provided" >&2
    echo "" >&2
    echo "Usage: $(basename "$0") [--workers N] [--id ID] <input_file>" >&2
    echo "" >&2
    echo "  <input_file>  File with one source path per line" >&2
    echo "  --workers N   Number of max workers (default: 14)" >&2
    echo "  --id ID       Output ID, e.g. idr0027-dickerson-chromatin/experimentA (prompted if not provided)" >&2
    exit 1
fi

if [[ ! -f "$input_file" ]]; then
    echo "Error: input file '$input_file' not found" >&2
    exit 1
fi

if [[ -z "$id" ]]; then
    read -rp "Enter ID (e.g. idr0027-dickerson-chromatin/experimentA): " id
fi

total=$(wc -l < "$input_file")
count=0

while IFS=$'\t' read -r target_dir filepath image_name extra; do
    count=$((count + 1))
    filename="${filepath##*/}"
    basename="${filename%.*}"
    zarr_name="${image_name:-$basename}"
    target_dir="${target_dir#*Dataset:name:}"
    mkdir -p "/data/output/${id}/${target_dir}"
    echo "[$count/$total] Converting $filepath to ${target_dir}/${zarr_name}.ome.zarr"
    bioformats2raw --ngff-version=0.5 --max_workers="$max_workers" --memo-directory=/data/memo "$filepath" "/data/output/${id}/${target_dir}/${zarr_name}.ome.zarr"
done < "$input_file"
