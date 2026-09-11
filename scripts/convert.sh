#!/bin/bash

max_workers=14
input_file=
output_dir=/data/output
bf2raw="${BF2RAW:-bioformats2raw}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --workers) max_workers="$2"; shift 2 ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            if [[ -z "$input_file" ]]; then
                input_file="$1"
            else
                output_dir="$1"
            fi
            shift ;;
    esac
done

if [[ -z "$input_file" ]]; then
    echo "Error: no input file provided" >&2
    echo "" >&2
    echo "Usage: $(basename "$0") [--workers N] <input_file> [<output_dir>]" >&2
    echo "" >&2
    echo "  <input_file>  filepaths.tsv as written by metadata.py" >&2
    echo "  <output_dir>  output root directory (default: /data/output)" >&2
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
    mkdir -p "${output_dir}/${target_dir}"
    bfparams=$(python bfparams.py "$filepath")
    command=("$bf2raw" --ngff-version=0.5 --downsample-type=AREA -c zstd --compression-properties='level=1' $bfparams --max_workers="$max_workers" --memo-directory=/data/memo "$filepath" "${output_dir}/${target_dir}/${zarr_name}")
    log_path="${output_dir}/${target_dir}/${zarr_name}.log"
    {
        printf 'Bioformats2raw %s\n' "$($bf2raw --version)"
        printf 'Command:'
        printf ' %q' "${command[@]}"
        printf '\n'
    } > "$log_path"
    if ! "${command[@]}" >> "$log_path" 2>&1; then
        failed_lines+=("$count")
        failed_lines_content+=("$line_content")
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
