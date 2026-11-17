#!/bin/bash
# Import sample OME-Zarr URLs using the active OMERO CLI session.
# Reuse the samples project and its named datasets, creating/linking as needed.
# Input: dataset name followed by a tab (or whitespace) and an HTTP(S) URL.
# Usage: sample_import.sh <sample_images.tsv>
# Rerunning skips images whose names already exist in the target dataset.

set -eu

if [[ $# -ne 1 || ! -f "$1" ]]; then
    echo "Usage: $(basename "$0") <sample_images.tsv>" >&2
    exit 1
fi
command -v omero >/dev/null 2>&1 || { echo "Error: omero CLI not found" >&2; exit 1; }

lookup_id() {
    local result ids count
    result=$(omero hql --style csv --limit 2 "$1") || return 1
    ids=$(printf '%s\n' "$result" | awk -F, 'NF == 2 {
        gsub(/["\r[:space:]]/, "", $1); gsub(/["\r[:space:]]/, "", $2)
        if ($1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/) print $2
    }')
    count=$(printf '%s\n' "$ids" | awk 'NF {n++} END {print n+0}')
    if [[ "$count" -gt 1 ]]; then
        echo "Error: multiple matching objects; resolve duplicate names before importing" >&2
        return 1
    fi
    printf '%s' "$ids"
}

object_id() {
    local object="$1" kind="$2"
    if [[ "$object" =~ ^${kind}:([0-9]+)$ ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    else
        echo "Error: unexpected object creation output: $object" >&2
        return 1
    fi
}

project_id=$(lookup_id "select p.id from Project p where p.name = 'idr-zarr-conversion Samples'")
if [[ -z "$project_id" ]]; then
    project=$(omero obj new Project name="idr-zarr-conversion Samples")
    project_id=$(object_id "$project" Project)
fi
omero obj get "Project:$project_id" >/dev/null
printf 'Project: %s\n' "$project_id"

failed=0
lineno=0
while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" == *$'\t'* ]]; then
        dataset="${line%%$'\t'*}"
        url="${line#*$'\t'}"
    else
        read -r dataset url <<< "$line"
    fi
    if [[ -z "$dataset" || ! "$url" =~ ^https?://[^[:space:]]+$ ]]; then
        echo "Error: invalid dataset/URL at line $lineno" >&2
        failed=1
        continue
    fi
    escaped="${dataset//\'/\'\'}"
    dataset_id=$(lookup_id "select d.id from Dataset d join d.projectLinks l where l.parent.id = $project_id and d.name = '$escaped'")
    if [[ -z "$dataset_id" ]]; then
        object=$(omero obj new Dataset "name=$dataset")
        dataset_id=$(object_id "$object" Dataset)
        omero obj new ProjectDatasetLink "parent=Project:$project_id" "child=Dataset:$dataset_id" >/dev/null
    fi
    omero obj get "Dataset:$dataset_id" >/dev/null
    name="${url%%\?*}"
    name="${name%%\#*}"
    name="${name%/}"
    name="${name##*/}"
    escaped_name="${name//\'/\'\'}"
    image_id=$(lookup_id "select i.id from Image i join i.datasetLinks l where l.parent.id = $dataset_id and i.name = '$escaped_name'")
    if [[ -n "$image_id" ]]; then
        printf 'Skipping %s: Image:%s already exists in Dataset:%s\n' "$name" "$image_id" "$dataset_id"
        continue
    fi
    printf 'Importing %s into Dataset:%s (%s)\n' "$name" "$dataset_id" "$dataset"
    if ! omero zarr import --target "$dataset_id" --name "$name" "$url"; then
        echo "Error: import failed at line $lineno" >&2
        failed=1
    fi
done < "$1"
exit "$failed"
