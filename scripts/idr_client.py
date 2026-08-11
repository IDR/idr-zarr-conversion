"""IDR OMERO JSON API and NCBI taxonomy helpers for the RO-Crate editor."""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

BASE_URL = "https://idr.openmicroscopy.org/api/v0"
WEBCLIENT = "https://idr.openmicroscopy.org/webclient"
WEBGATEWAY = "https://idr.openmicroscopy.org/webgateway"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def idr_get(path: str, **params) -> dict:
    """Make a single GET request to the IDR JSON API."""
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, params=params or None, timeout=30)
    resp.raise_for_status()
    return resp.json()


def idr_get_all(path: str, params: dict | None = None, limit: int = 1000) -> list:
    """Walk through paginated IDR list endpoints and return all data items."""
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


def extract_url(value: str) -> str:
    """Pull the first URL out of strings like 'CC BY 4.0 https://...'."""
    if not value:
        return ""
    m = re.search(r"https?://\S+", value)
    if m:
        return m.group(0).rstrip(".")
    return value


def parse_idr_url(url: str) -> tuple[str, int] | None:
    """Extract ('project'|'screen', id) from IDR webclient URLs or bare IDs."""
    if not url:
        return None

    # Direct numeric pairs like project-151, screen-3
    m = re.search(r"\b(project|screen)-(\d+)\b", url, re.I)
    if m:
        return m.group(1).lower(), int(m.group(2))

    # ?show=project-151 or ?show=screen-3
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    show = qs.get("show", [None])[0]
    if show:
        m = re.match(r"(project|screen)-(\d+)$", show, re.I)
        if m:
            return m.group(1).lower(), int(m.group(2))

    return None


def find_containers(name: str) -> list[tuple[str, dict]]:
    """Find an IDR project or screen by exact name."""
    matches = []
    for screen in idr_get_all("/m/screens/"):
        if screen.get("Name") == name:
            matches.append(("screen", screen))
    for project in idr_get_all("/m/projects/"):
        if project.get("Name") == name:
            matches.append(("project", project))
    return matches


def get_container(container_type: str, container_id: int) -> dict:
    """Fetch a project or screen by ID."""
    return idr_get(f"/m/{container_type}s/{container_id}/")


def get_annotations(container_type: str, container_id: int) -> dict:
    """Fetch map annotations for a project/screen and return as a flat dict."""
    resp = requests.get(
        f"{WEBCLIENT}/api/annotations/",
        params={"type": "map", container_type: container_id},
        timeout=30,
    )
    result = {}
    if resp.ok:
        for ann in resp.json().get("annotations", []):
            for kv in ann.get("values", []):
                if len(kv) >= 2:
                    result[kv[0]] = kv[1]
    return result


def get_children(container_type: str, container_id: int) -> list[dict]:
    """Return datasets (project) or plates (screen) for a container."""
    if container_type == "screen":
        return idr_get_all(f"/m/screens/{container_id}/plates/")
    return idr_get_all(f"/m/projects/{container_id}/datasets/")


def get_images(container_type: str, child_id: int) -> list[dict]:
    """Return the images belonging to a dataset or plate."""
    images = []
    if container_type == "screen":
        wells = idr_get_all(f"/m/plates/{child_id}/wells/")
        for well in wells:
            for ws in well.get("WellSamples", []):
                images.append(ws["Image"])
    else:
        images = idr_get_all(f"/m/datasets/{child_id}/images/")
    return images


def get_image_path(image_id: int) -> str:
    """Fetch the file-system-like image name from the webclient."""
    resp = requests.get(
        f"{WEBCLIENT}/imgData/{image_id}/",
        params={"fmt": "json"},
        timeout=30,
    )
    if resp.ok:
        name = resp.json().get("meta", {}).get("imageName", "")
        if name:
            return name
    return str(image_id)


def normalise_path(path: str) -> str:
    """Normalise a client-side original file path to a POSIX filesystem path."""
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    return path


def get_client_path(image_id: int) -> str | None:
    """Fetch the real client-side (upload-time) filesystem path for an image.

    Uses OMERO's ``original_file_paths`` webgateway endpoint, which reports
    both the ``server`` (managed repository) and ``client`` (as-uploaded)
    paths for the files backing an image's fileset. Returns None if the
    path can't be resolved (e.g. no permission, image has no fileset).
    """
    try:
        resp = requests.get(
            f"{WEBGATEWAY}/original_file_paths/{image_id}/", timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None
    client_paths = data.get("client", [])
    if not client_paths:
        return None
    return normalise_path(client_paths[0])


def zarr_name(client_path: str) -> str:
    """Turn an image file name into an OME-Zarr file name.

    Strips the file extension, treating a trailing ``.ome.tif``/``.ome.tiff``
    as a single extension (``Path.stem`` would otherwise only strip the
    ``.tiff`` part, leaving e.g. ``image.ome.ome.zarr``).
    """
    name = Path(client_path).name
    m = re.match(r"^(.*)\.ome\.tiff?$", name, re.I)
    if m:
        return m.group(1) + ".ome.zarr"
    return Path(name).stem + ".ome.zarr"


def load_study(study_input: str) -> dict:
    """Load a study from a URL, a numeric project/screen ID, or a name."""
    parsed = parse_idr_url(study_input)
    if parsed:
        container_type, container_id = parsed
        container = get_container(container_type, container_id).get("data", {})
    else:
        matches = find_containers(study_input)
        if not matches:
            raise ValueError(f"No IDR project or screen named '{study_input}'")
        if len(matches) > 1:
            raise ValueError(
                f"Multiple matches for '{study_input}'; use an IDR URL or project-XX/screen-XX"
            )
        container_type, matched = matches[0]
        container_id = matched["@id"]
        container = get_container(container_type, container_id).get("data", {})

    annotations = get_annotations(container_type, container_id)
    children = get_children(container_type, container_id)

    return {
        "type": container_type,
        "container": {
            "@id": container.get("@id"),
            "Name": container.get("Name", ""),
            "Description": container.get("Description", ""),
        },
        "annotations": annotations,
        "children": [
            {
                "@id": c.get("@id"),
                "Name": c.get("Name", ""),
                "Description": c.get("Description", ""),
            }
            for c in children
        ],
    }


def get_child_files(
    container_type: str, child_id: int, child_name: str = "", letter_dir: str = ""
) -> list[dict]:
    """Return file-list records (RO-Crate ``file_list.tsv`` rows) for a
    dataset/plate.

    `child_name` is only required for screens so we can name the plate file
    without making an extra API call. `letter_dir` (e.g. ``experimentA`` or
    ``screenA``) is prepended to every returned `path`, rooting it the same
    way `file_list.tsv` is rooted relative to the RO-Crate output directory.
    """
    prefix = f"{letter_dir}/" if letter_dir else ""

    if container_type == "screen":
        z = zarr_name(child_name)
        return [{"path": f"{prefix}{z}", "zarr_name": z}]

    files = []
    for img in get_images(container_type, child_id):
        image_id = img["@id"]
        image_name = img.get("Name") or get_image_path(image_id)
        client_path = get_client_path(image_id)
        z = zarr_name(client_path or image_name)
        files.append({
            "path": f"{prefix}{child_name}/{z}",
            "zarr_name": z,
            "image_id": image_id,
            "image_name": image_name,
        })
    return files


def ncbi_taxon(name: str) -> dict | None:
    """Look up an NCBI Taxon by scientific/common name."""
    if not name:
        return None

    r = requests.get(
        NCBI_ESEARCH,
        params={"db": "taxonomy", "term": name, "retmode": "json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return None

    r2 = requests.get(
        NCBI_ESUMMARY,
        params={"db": "taxonomy", "id": ids[0], "retmode": "json"},
        timeout=30,
    )
    r2.raise_for_status()
    summary = r2.json().get("result", {}).get(ids[0], {})
    if not summary:
        return None

    return {
        "taxid": summary.get("taxid"),
        "scientificName": summary.get("scientificname"),
        "commonName": summary.get("commonname") or None,
    }
