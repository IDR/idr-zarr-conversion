#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: expand.sh <tsv_file|url>

Replace the second column of a TSV (expected to be a directory) with the
full path of the first regular file found under that directory.

The input may be a local file path or an http(s) URL.
EOF
}

if [[ $# -ne 1 ]]; then
    usage >&2
    exit 1
fi

input="$1"

if [[ "$input" == http://* || "$input" == https://* ]]; then
    curl -sSL "$input"
else
    cat "$input"
fi | while IFS=$'\t' read -r col1 dir rest; do
    if [[ -d "$dir" ]]; then
        file=$(find "$dir" -type f -print -quit)
        if [[ -n "$file" ]]; then
            dir="$file"
        fi
    fi
    printf '%s\t%s\t%s\n' "$col1" "$dir" "$rest"
done
