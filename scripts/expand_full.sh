#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: expand_full.sh [--files ext1,ext2,...] <tsv_file|url>

Replace the second column of a TSV (expected to be a directory) with one
line per immediate file found under that directory. Non-directory paths are
passed through unchanged.

Optional:
  --files ext1,ext2,...  Only include files with the given extensions.

The input may be a local file path or an http(s) URL.
EOF
}

input=""
ext_filter=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --files)
            ext_filter="$2"
            shift 2
            ;;
        --files=*)
            ext_filter="${1#*=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            input="$1"
            shift
            ;;
    esac
done

if [[ -z "$input" ]]; then
    usage >&2
    exit 1
fi

find_files() {
    local dir="$1"
    if [[ -z "$ext_filter" ]]; then
        find "$dir" -maxdepth 1 -type f
    else
        local exts=()
        IFS=',' read -ra exts <<< "$ext_filter"
        local name_pred=()
        for ext in "${exts[@]}"; do
            name_pred+=(-o -name "*.${ext}")
        done
        find "$dir" -maxdepth 1 -type f \( -false "${name_pred[@]}" \)
    fi | sort
}

if [[ "$input" == http://* || "$input" == https://* ]]; then
    curl -sSL "$input"
else
    cat "$input"
fi | while IFS=$'\t' read -r col1 dir rest; do
    if [[ -d "$dir" ]]; then
        while IFS= read -r file; do
            if [[ -n "$rest" ]]; then
                printf '%s\t%s\t%s\n' "$col1" "$file" "$rest"
            else
                printf '%s\t%s\n' "$col1" "$file"
            fi
        done < <(find_files "$dir")
    else
        if [[ -n "$rest" ]]; then
            printf '%s\t%s\t%s\n' "$col1" "$dir" "$rest"
        else
            printf '%s\t%s\n' "$col1" "$dir"
        fi
    fi
done
