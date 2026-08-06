#!/usr/bin/env python3
"""Generate a conversion TSV from an IDR project or screen URL.

The script queries the IDR OMERO JSON API and outputs one TSV row per image
(project) or per plate (screen). The second column is the original filesystem
path taken from the webclient's `meta.imageName`, normalised to start with `/`.
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlparse

BASE_URL = "https://idr.openmicroscopy.org/api/v0"
WEBCLIENT = "https://idr.openmicroscopy.org/webclient"
WEBGATEWAY = "https://idr.openmicroscopy.org/webgateway"
DEFAULT_LIMIT = 1000


def parse_idr_url(url: str) -> tuple[str, int] | None:
    """Extract ('project'|'screen', id) from IDR webclient URLs or bare IDs."""
    m = re.search(r"\b(project|screen)-(\d+)\b", url, re.I)
    if m:
        return m.group(1).lower(), int(m.group(2))

    parsed = urlparse(url)
    show = parse_qs(parsed.query).get("show", [None])[0]
    if show:
        m = re.match(r"(project|screen)-(\d+)$", show, re.I)
        if m:
            return m.group(1).lower(), int(m.group(2))

    return None


def json_get(url: str, params: dict | None = None) -> dict:
    """Perform a GET request and return the decoded JSON response."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def idr_get(path: str, **params) -> dict:
    return json_get(f"{BASE_URL}{path}", params or None)


def idr_get_all(path: str, params: dict | None = None, limit: int = DEFAULT_LIMIT) -> list:
    """Walk through a paginated IDR list endpoint and return all items."""
    out = []
    offset = 0
    params = dict(params or {})
    while True:
        page_params = {**params, "limit": limit, "offset": offset}
        data = idr_get(path, **page_params)
        items = data.get("data", [])
        out.extend(items)
        total = data.get("meta", {}).get("totalCount") or 0
        if len(out) >= total or not items:
            break
        offset += limit
    return out


def normalise_path(path: str) -> str:
    """Ensure the returned file path starts with a '/'."""
    if path and not path.startswith("/"):
        return "/" + path
    return path


def get_image_path(image_id: int) -> str | None:
    """Return the original imported filesystem path for an image, or None."""
    url = f"{WEBGATEWAY}/original_file_paths/{image_id}/"
    try:
        data = json_get(url)
    except urllib.error.URLError:
        return None
    client_paths = data.get("client", [])
    if client_paths:
        return normalise_path(client_paths[0])

    # Fallback to the image name endpoint if original paths are unavailable.
    url = f"{WEBCLIENT}/imgData/{image_id}/"
    try:
        data = json_get(url, {"fmt": "json"})
    except urllib.error.URLError:
        return None
    name = data.get("meta", {}).get("imageName", "")
    if not name:
        return None
    return normalise_path(name)


def project_rows(project_id: int):
    for dataset in idr_get_all(f"/m/projects/{project_id}/datasets/"):
        dataset_name = dataset.get("Name", "")
        for image in idr_get_all(f"/m/datasets/{dataset['@id']}/images/"):
            image_id = image["@id"]
            image_name = image.get("Name") or str(image_id)
            path = get_image_path(image_id)
            if path is None:
                continue
            yield (dataset_name, path, image_name)


def screen_rows(screen_id: int):
    for plate in idr_get_all(f"/m/screens/{screen_id}/plates/"):
        plate_name = plate.get("Name", "")
        data = idr_get(f"/m/plates/{plate['@id']}/wells/", limit=1, offset=0)
        wells = data.get("data", [])
        if not wells:
            continue
        well_samples = wells[0].get("WellSamples", [])
        if not well_samples:
            continue
        image_id = well_samples[0]["Image"]["@id"]
        path = get_image_path(image_id)
        if path is None:
            continue
        yield ("", path, plate_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a TSV from an IDR project or screen URL."
    )
    parser.add_argument(
        "url",
        help="IDR webclient URL, e.g. https://idr.openmicroscopy.org/webclient/?show=project-904",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="Output TSV file (default: stdout)",
    )
    parser.add_argument(
        "--drop-extension",
        "-d",
        action="store_true",
        help="Strip the file extension from the image name in the third column (project rows only).",
    )
    args = parser.parse_args(argv)
    out_file = sys.stdout if args.output == "-" else open(args.output, "w", newline="")

    parsed = parse_idr_url(args.url)
    if parsed is None:
        print(f"Error: could not parse IDR project/screen URL: {args.url}", file=sys.stderr)
        return 1

    container_type, container_id = parsed
    if container_type == "project":
        rows = project_rows(container_id)
    else:
        rows = screen_rows(container_id)

    writer = csv.writer(out_file, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    count = 0
    try:
        for row in rows:
            if args.drop_extension and container_type == "project":
                dataset_name, path, image_name = row
                image_name = os.path.splitext(image_name)[0]
                row = (dataset_name, path, image_name)
            writer.writerow(row)
            count += 1
            if count % 100 == 0:
                print(f"Processed {count} rows...", file=sys.stderr)
    except BrokenPipeError:
        pass

    if out_file is not sys.stdout:
        out_file.close()

    print(f"Total rows written: {count}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
