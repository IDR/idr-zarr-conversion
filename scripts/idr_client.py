"""IDR OMERO JSON API and NCBI taxonomy helpers for the RO-Crate editor."""

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

BASE_URL = "https://idr.openmicroscopy.org/api/v0"
WEBCLIENT = "https://idr.openmicroscopy.org/webclient"
WEBGATEWAY = "https://idr.openmicroscopy.org/webgateway"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# NCBI E-utilities asks for no more than ~3 requests/sec without an API key.
_MIN_NCBI_INTERVAL = 0.35
_LAST_NCBI_REQUEST = 0.0
_TAXON_CACHE: dict[str, dict | None] = {}


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


# Companion/sidecar files that can show up alongside the real image file in
# a fileset (e.g. MicroManager's ``*_metadata.txt``) but aren't themselves
# importable by Bio-Formats.
_SIDECAR_SUFFIXES = (".txt", ".log")


def _is_zarr_source(client_paths: list[str]) -> bool:
    """True if a fileset's client paths point into an OME-Zarr store.

    Some IDR studies deposit images that are already OME-Zarr (e.g. served
    from S3), rather than a raw microscopy file bioformats2raw can convert.
    Such images should be skipped entirely rather than treated as a source
    file to convert.
    """
    return any(".zarr/" in p or p.rstrip("/").endswith(".zarr") for p in client_paths)


def get_source_path(image_id: int) -> tuple[str | None, bool]:
    """Fetch the client-side source path for an image and flag OME-Zarr URLs.

    Uses OMERO's ``original_file_paths`` webgateway endpoint. Returns
    ``(path, is_zarr_source)``. ``is_zarr_source`` is True when the source is
    an already-converted OME-Zarr URL (e.g. an S3 URL) that should not be run
    through bioformats2raw. Returns ``(None, False)`` if no usable path can be
    resolved.
    """
    try:
        resp = requests.get(
            f"{WEBGATEWAY}/original_file_paths/{image_id}/", timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None, False
    client_paths = data.get("client", [])
    if not client_paths:
        return None, False
    is_zarr = _is_zarr_source(client_paths)
    for path in client_paths:
        name = path.rsplit("/", 1)[-1]
        if not name.lower().endswith(_SIDECAR_SUFFIXES):
            selected = path
            break
    else:
        selected = client_paths[0]
    if is_zarr:
        # The webgateway reports a .zattrs path inside the Zarr store; the
        # file list needs the store URL itself and uses the new BIA object
        # storage endpoint.
        zarr_url = selected.rstrip("/")
        if zarr_url.endswith("/.zattrs"):
            zarr_url = zarr_url[: -len("/.zattrs")]
        zarr_url = re.sub(
            r"^https?://uk1s3\.embassy\.ebi\.ac\.uk/bia-integrator-data",
            "https://livingobjects.ebi.ac.uk/bioimaging-integrator-data",
            zarr_url,
            count=1,
            flags=re.I,
        )
        return zarr_url, True
    return normalise_path(selected), False


def get_client_path(image_id: int) -> str | None:
    """Fetch the real client-side filesystem path for an image.

    Returns None if the path can't be resolved or if the source is an
    OME-Zarr store (nothing to convert).
    """
    path, is_zarr = get_source_path(image_id)
    if path is None or is_zarr:
        return None
    return path


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


def _safe_zarr_base(name: str, fallback_id: int | str) -> str:
    """Sanitise an OMERO image/plate name for use as a Zarr directory stem.

    Unsafe filesystem characters are replaced with underscores, leading/trailing
    whitespace and separators are stripped, and a numeric fallback is used when
    the resulting name is empty.
    """
    base = str(name).strip()
    if not base:
        base = str(fallback_id)
    base = re.sub(r"[^A-Za-z0-9_ .-]+", "_", base)
    base = re.sub(r"_+", "_", base)
    base = base.strip("_. ")
    if not base:
        base = str(fallback_id)
    return base


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
        # Use the first well's image to get a source file path for convert.sh
        first_page = idr_get(f"/m/plates/{child_id}/wells/", limit=1, offset=0)
        wells = first_page.get("data", [])
        source_path = None
        image_id = None
        if wells:
            well_samples = wells[0].get("WellSamples", [])
            if well_samples:
                image_id = well_samples[0]["Image"]["@id"]
                source_path, is_zarr = get_source_path(image_id)
        if source_path is None:
            return []
        if is_zarr:
            return [{"path": source_path, "zarr_name": None, "source_path": source_path, "is_zarr_source": True}]
        z = _safe_zarr_base(child_name, child_id) + ".ome.zarr"
        return [{"path": f"{prefix}{z}", "zarr_name": z, "source_path": source_path, "is_zarr_source": False}]

    files = []
    for img in get_images(container_type, child_id):
        image_id = img["@id"]
        source_path, is_zarr = get_source_path(image_id)
        if source_path is None:
            continue
        if is_zarr:
            files.append({
                "path": source_path,
                "zarr_name": None,
                "source_path": source_path,
                "is_zarr_source": True,
                "image_id": image_id,
                "image_name": img.get("Name", ""),
            })
            continue
        z = _safe_zarr_base(img.get("Name", ""), image_id) + ".ome.zarr"
        files.append({
            "path": f"{prefix}{child_name}/{z}",
            "zarr_name": z,
            "source_path": source_path,
            "is_zarr_source": False,
            "image_id": image_id,
            "image_name": img.get("Name", ""),
        })
    return files


def _ncbi_get(url: str, params: dict, retries: int = 3) -> requests.Response:
    """Make a rate-limited NCBI E-utilities request, retrying on 429."""
    global _LAST_NCBI_REQUEST

    for attempt in range(retries):
        elapsed = time.monotonic() - _LAST_NCBI_REQUEST
        if elapsed < _MIN_NCBI_INTERVAL:
            time.sleep(_MIN_NCBI_INTERVAL - elapsed)

        r = requests.get(url, params=params, timeout=30)
        _LAST_NCBI_REQUEST = time.monotonic()
        if r.status_code == 429 and attempt < retries - 1:
            time.sleep(1)
            continue
        r.raise_for_status()
        return r

    # Should only be reached if every attempt returned 429.
    r.raise_for_status()
    return r


def ncbi_taxon(name: str) -> dict | None:
    """Look up an NCBI Taxon by scientific/common name."""
    if not name:
        return None
    if name in _TAXON_CACHE:
        return _TAXON_CACHE[name]

    r = _ncbi_get(
        NCBI_ESEARCH,
        params={"db": "taxonomy", "term": name, "retmode": "json"},
    )
    data = r.json()
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        _TAXON_CACHE[name] = None
        return None

    r2 = _ncbi_get(
        NCBI_ESUMMARY,
        params={"db": "taxonomy", "id": ids[0], "retmode": "json"},
    )
    summary = r2.json().get("result", {}).get(ids[0], {})
    if not summary:
        _TAXON_CACHE[name] = None
        return None

    result = {
        "taxid": summary.get("taxid"),
        "scientificName": summary.get("scientificname"),
        "commonName": summary.get("commonname") or None,
    }
    _TAXON_CACHE[name] = result
    return result
