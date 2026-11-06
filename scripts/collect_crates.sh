#!/bin/bash
# Collect RO-Crate files (ro-crate-metadata.json + file_list.tsv) from
# study output directories into a single directory, one subdir per study.
#
# Usage: collect_crates.sh <input_dir> <output_dir>
#   <input_dir>   directory containing idrXXXX-* study dirs (e.g. /overflow)
#   <output_dir>  destination (e.g. /home/rocky/ro-crates)

set -u

input_dir="${1:-}"
output_dir="${2:-}"

if [[ -z "$input_dir" || -z "$output_dir" ]]; then
    echo "Usage: $(basename "$0") <input_dir> <output_dir>" >&2
    exit 1
fi
if [[ ! -d "$input_dir" ]]; then
    echo "Error: input dir '$input_dir' not found" >&2
    exit 1
fi

for study_dir in "$input_dir"/*/; do
    name=$(basename "$study_dir")
    for f in ro-crate-metadata.json file_list.tsv; do
        if [[ -f "${study_dir}${f}" ]]; then
            mkdir -p "${output_dir}/${name}"
            cp "${study_dir}${f}" "${output_dir}/${name}/"
        fi
    done
done
