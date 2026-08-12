#!/usr/bin/env python3
"""Generate conversion inputs and a minimal BIA RO-Crate from an IDR study.txt URL.

The script downloads an IDR study.txt file, parses its tab-delimited metadata,
derives the OMERO project/screen name(s), fetches the image/plate list from
the IDR OMERO JSON API, and writes three files to --output-dir:

- ``filepaths.tsv`` — a 3-column, no-header TSV consumed by ``convert.sh`` to
  drive the bioformats2raw conversion. Columns: ``experiment<A/B/...>`` or
  ``experiment<A/B/...>/<Dataset name>`` (screens/projects respectively), the
  real filesystem path of the source image/plate file, and the
  ``<file name>.ome.zarr`` name to convert it to.
- ``ro-crate-metadata.json`` — a minimal BIA RO-Crate for the study.
- ``file_list.tsv`` — the RO-Crate's file list
"""

import argparse
import csv
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

from idr_client import (
    find_containers,
    get_children,
    get_child_files,
    get_client_path,
    get_container,
    get_images,
    idr_get,
    ncbi_taxon,
    zarr_name,
)

BIA_CONTEXT_PATH = Path(__file__).resolve().parent / "bia_context.json"
DEFAULT_LICENSE = "https://creativecommons.org/licenses/by/4.0/"


def load_bia_context() -> list | dict:
    with open(BIA_CONTEXT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_study_txt(url: str) -> str:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text


def normalise_key(key: str) -> str:
    return key.strip().strip("#")


def parse_study_txt(text: str) -> tuple[dict, list]:
    """Parse an IDR study.txt into top-level metadata and component sections.

    Component sections start with a line like ``Experiment Number\t1`` or
    ``Screen Number\t1`` and contain per-component metadata such as
    ``Comment[IDR Experiment Name]``.
    """
    top = {}
    components = []
    current = None
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    for row in reader:
        if not row or len(row) < 2:
            continue
        key = normalise_key(row[0])
        value = row[1].strip() if len(row) > 1 else ""
        if not key:
            continue
        m = re.match(r"^(Experiment|Screen) Number$", key, re.I)
        if m:
            current = {
                "type": m.group(1).lower(),
                "number": value,
                "data": {},
            }
            components.append(current)
            continue
        # A study/experiment/screen can list more than one imaging method on
        # the same row (extra tab-separated columns), e.g.
        # "Experiment Imaging Method\tlight sheet fluorescence microscopy\tSPIM".
        if re.match(r"^(Study|Experiment|Screen) Imaging Method$", key, re.I):
            value = ", ".join(v.strip() for v in row[1:] if v.strip())
        if current is not None:
            current["data"][key] = value
        else:
            top[key] = value
    return top, components


def extract_url(value: str) -> str:
    """Return the first URL in a string, or the original string if none."""
    if not value:
        return ""
    m = re.search(r"https?://\S+", value)
    if m:
        return m.group(0).rstrip(".")
    return value


def component_name(component: dict) -> str | None:
    """Return the OMERO container name for a parsed component."""
    if component["type"] == "screen":
        return component["data"].get("Comment[IDR Screen Name]", "").strip() or None
    return component["data"].get("Comment[IDR Experiment Name]", "").strip() or None


def select_components(components: list) -> list[tuple[dict, str]]:
    """Return the (component, container_name) pairs to process.

    Every named experiment/screen component in the study is processed (a
    study can have several, e.g. idr0038 has experimentA/B/C, each backed
    by its own OMERO project/screen).
    """
    named = [(c, component_name(c)) for c in components]
    return [(c, name) for c, name in named if name]


def derive_fallback_container_name(study: dict) -> str:
    """Derive a single OMERO project/screen name for studies with no
    Experiment/Screen Number sections in study.txt."""
    for key in ("Comment[IDR Experiment Name]", "Comment[IDR Screen Name]"):
        value = study.get(key, "").strip()
        if value:
            return value

    accession = study.get("Comment[IDR Study Accession]", "").strip()
    if accession:
        experiments = study.get("Study Experiments Number", "1").strip() or "1"
        screens = study.get("Study Screens Number", "0").strip() or "0"
        if int(experiments or 0) > 1 or int(screens or 0) > 1:
            raise ValueError(
                "Study has multiple experiments/screens but no component name; "
                "cannot derive a single container name"
            )
        if int(screens or 0) > 0:
            return f"{accession}/screenA"
        return f"{accession}/experimentA"
    raise ValueError(
        "Could not derive OMERO container name from study.txt. "
        "Expected Comment[IDR Experiment Name] or Comment[IDR Screen Name]."
    )


def derive_accession_id(study: dict, container_name: str) -> str:
    """Return the study's plain IDR accession, e.g. 'IDR0027' (not 'IDR0027A')."""
    accession = study.get("Comment[IDR Study Accession]", "").strip()
    if accession:
        return accession.upper()
    return container_name.split("/")[0].split("-")[0].upper()


def collect_imaging_methods(study: dict, containers_info: list) -> list[str]:
    """Gather every distinct imaging method across all processed components,
    falling back to the top-level Study Type if none are found."""
    methods = []
    for entry in containers_info:
        component = entry["component"]
        if not component:
            continue
        is_screen = component["type"] == "screen"
        value = component["data"].get(
            "Screen Imaging Method" if is_screen else "Experiment Imaging Method", ""
        )
        for method in value.split(","):
            method = method.strip()
            if method and method not in methods:
                methods.append(method)
    if not methods:
        study_type = study.get("Study Type", "").strip()
        if study_type:
            methods.append(study_type)
    return methods


def build_authors(authors_str: str) -> list:
    authors = []
    for i, name in enumerate(authors_str.split(",")):
        name = name.strip()
        if name:
            authors.append({
                "@id": f"#author-{i}",
                "@type": ["Person", "bia:Contributor"],
                "name": name,
                "address": None,
                "website": None,
                "memberOf": [],
                "role": ["author"],
                "email": None,
            })
    return authors


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def container_letter_dir(container_name: str) -> str:
    """Return the last path segment of an OMERO container name.

    IDR containers are named ``<study accession>/<experiment|screen><Letter>``
    (e.g. ``idr0038-held-kidneylightsheet/experimentA``), so this is simply
    ``experimentA`` or ``screenA``.
    """
    return container_name.rsplit("/", 1)[-1]


def filepaths_rows(containers_info: list):
    """Yield (target_dir, source_path, zarr_name) rows for convert.sh.

    `target_dir` is rooted at the IDR study accession, e.g.
    ``<study>/<experimentX>/<dataset>`` for projects or
    ``<study>/<screenX>`` for screens, so `convert.sh` can write directly
    under a shared output root without a separate ``--id``. Rows are
    derived from each image's real client-side filesystem path;
    images/plates whose path can't be resolved are skipped, as are exact
    duplicate rows (multiple OMERO images can point at the same underlying
    file).
    """
    seen = set()
    for entry in containers_info:
        container_type = entry["container_type"]
        container_name = entry["container_name"]
        container_id = entry["container"]["@id"]

        for child in get_children(container_type, container_id):
            child_id = child["@id"]

            if container_type == "screen":
                first_page = idr_get(f"/m/plates/{child_id}/wells/", limit=1, offset=0)
                wells = first_page.get("data", [])
                if not wells:
                    continue
                well_samples = wells[0].get("WellSamples", [])
                if not well_samples:
                    continue
                image_id = well_samples[0]["Image"]["@id"]
                client_path = get_client_path(image_id)
                if client_path is None:
                    continue
                row = (container_name, client_path, zarr_name(client_path))
            else:
                dataset_name = child.get("Name", "")
                target_dir = f"{container_name}/{dataset_name}"
                for image in get_images(container_type, child_id):
                    client_path = get_client_path(image["@id"])
                    if client_path is None:
                        continue
                    row = (target_dir, client_path, zarr_name(client_path))
                    if row in seen:
                        continue
                    seen.add(row)
                    yield row
                continue

            if row in seen:
                continue
            seen.add(row)
            yield row


def build_crate(
    study: dict,
    containers_info: list,
    output_dir: str,
):
    """Build the RO-Crate covering every container in `containers_info`.

    Each entry is a dict with keys ``component`` (parsed component dict or
    None), ``container_type``, ``container_name`` and ``container`` (the
    OMERO project/screen data).
    """
    title = study.get("Study Title", "").strip()
    description = study.get("Study Description", "").strip()
    organism = study.get("Study Organism", "").strip()
    keywords = [k.strip() for k in study.get("Study Key Words", "").split(",") if k.strip()]

    imaging_methods = collect_imaging_methods(study, containers_info)
    for method in reversed(imaging_methods):
        if method not in keywords:
            keywords.insert(0, method)

    license_url = extract_url(study.get("Study License URL", study.get("Study License", ""))) or DEFAULT_LICENSE
    release_date = study.get("Study Public Release Date", "").strip()
    data_doi = extract_url(study.get("Study Data DOI", "").strip()) or None
    pub_doi = extract_url(study.get("Study DOI", "").strip()) or None
    pubmed_id = study.get("Study PubMed ID", "").strip() or None
    pub_title = study.get("Study Publication Title", "").strip()
    authors_str = study.get("Study Author List", "").strip()

    author_entities = build_authors(authors_str)
    author_refs = [{"@id": a["@id"]} for a in author_entities]
    accession_id = derive_accession_id(study, containers_info[0]["container_name"])

    graph = []

    # RO-Crate metadata descriptor
    graph.append({
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
        "about": {"@id": "./"},
    })

    has_part = []
    dataset_entities = []
    file_list_rows = []
    seen_file_paths = set()

    # BioSample/Taxon placeholders, linked from every dataset
    bio_sample_ref = []
    if organism:
        taxon = ncbi_taxon(organism)
        if taxon and taxon.get("taxid"):
            taxon_id = f"NCBI:txid{taxon['taxid']}"
            scientific_name = taxon.get("scientificName") or organism
            common_name = taxon.get("commonName")
        else:
            taxon_id = f"#taxon-{slugify(organism)}"
            scientific_name = organism
            common_name = None

        bio_sample_id = "#biosample-1"
        graph.append({
            "@id": taxon_id,
            "@type": "bia:Taxon",
            "scientificName": scientific_name,
            "commonName": common_name,
        })
        graph.append({
            "@id": bio_sample_id,
            "@type": "bia:BioSample",
            "name": organism,
            "description": organism,
            "organismClassification": [{"@id": taxon_id}],
        })
        bio_sample_ref = [{"@id": bio_sample_id}]

    # Datasets / plates, across every container in the study
    for entry in containers_info:
        container_type = entry["container_type"]
        container_name = entry["container_name"]
        container_id = entry["container"]["@id"]

        children = get_children(container_type, container_id)
        for child in children:
            child_id = child["@id"]
            child_name = child.get("Name", "")
            ds_id = f"#{'Plate' if container_type == 'screen' else 'Dataset'}-{child_id}"

            dataset_entity = {
                "@id": ds_id,
                "@type": ["Dataset", "bia:Dataset"],
                "name": child_name,
                "description": child.get("Description", ""),
                "associatedBiologicalEntity": bio_sample_ref,
                "associatedSpecimenImagingPreparationProtocol": [],
                "associatedSpecimen": None,
                "associatedCreationProcess": None,
                "associatedSourceImage": [],
                "associatedImageAcquisitionProtocol": [],
                "associatedAnnotationMethod": [],
                "associatedImageAnalysisMethod": [],
                "associatedImageCorrelationMethod": [],
                "associatedProtocol": [],
            }
            dataset_entities.append(dataset_entity)
            has_part.append({"@id": ds_id})

            for f in get_child_files(container_type, child_id, child_name, container_letter_dir(container_name)):
                if f["path"] in seen_file_paths:
                    continue
                seen_file_paths.add(f["path"])
                file_list_rows.append({
                    "file_path": f["path"],
                    "dataset": ds_id,
                    "type": "bia:Image",
                })

    # Root Study entity
    root_entity = {
        "@id": "./",
        "@type": ["Dataset", "bia:Study"],
        "name": title or accession_id,
        "description": description,
        "license": license_url,
        "datePublished": release_date,
        "author": author_refs,
        "keywords": keywords,
        "acknowledgement": None,
        "hasPart": has_part,
        "accessionId": accession_id,
        "funding": [],
        "seeAlso": [],
    }
    if data_doi:
        root_entity["doi"] = data_doi
    if pubmed_id:
        root_entity["pubmedId"] = pubmed_id
    if imaging_methods:
        root_entity["imagingMethodName"] = imaging_methods

    if pub_doi:
        root_entity["relatedPublication"] = [{"@id": pub_doi}]
        graph.append({
            "@id": pub_doi,
            "@type": "bia:Publication",
            "name": pub_title,
            "doi": pub_doi,
            "pubmedId": pubmed_id,
            "authorNames": authors_str,
        })

    graph.extend(author_entities)
    graph.extend(dataset_entities)
    graph.append(root_entity)

    # File list schema and entity (only if files exist)
    if file_list_rows:
        graph.append({
            "@id": "_:ts0",
            "@type": ["csvw:Schema"],
            "column": [
                {"@id": "_:col0"},
                {"@id": "_:col1"},
                {"@id": "_:col2"},
            ],
        })
        graph.append({
            "@id": "_:col0",
            "@type": ["csvw:Column"],
            "columnName": "file_path",
            "propertyUrl": "http://bia/filePath",
        })
        graph.append({
            "@id": "_:col1",
            "@type": ["csvw:Column"],
            "columnName": "dataset",
            "propertyUrl": "http://schema.org/isPartOf",
        })
        graph.append({
            "@id": "_:col2",
            "@type": ["csvw:Column"],
            "columnName": "type",
            "propertyUrl": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        })
        graph.append({
            "@id": "file_list.tsv",
            "@type": ["File", "bia:FileList", "csvw:Table"],
            "tableSchema": {"@id": "_:ts0"},
        })
        root_entity["hasPart"].append({"@id": "file_list.tsv"})

    crate_doc = {
        "@context": load_bia_context(),
        "@graph": graph,
    }

    os.makedirs(output_dir, exist_ok=True)

    tsv_path = Path(output_dir) / "file_list.tsv"
    with open(tsv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["file_path", "dataset", "type"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(file_list_rows)

    out_path = Path(output_dir) / "ro-crate-metadata.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(crate_doc, f, indent=2, ensure_ascii=False)

    print(f"RO-Crate written to {out_path}")
    print(f"File list written to {tsv_path} ({len(file_list_rows)} rows)")


def write_filepaths_tsv(containers_info: list, output_dir: str) -> int:
    path = Path(output_dir) / "filepaths.tsv"
    count = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        for row in filepaths_rows(containers_info):
            writer.writerow(row)
            count += 1
    print(f"Filepaths written to {path} ({count} rows)")
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Generate filepaths.tsv, a minimal BIA RO-Crate, and file_list.tsv "
        "from an IDR study.txt URL."
    )
    parser.add_argument(
        "study_url",
        help="URL of the IDR study.txt file (e.g. a GitHub raw URL).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=".",
        help="Directory to write filepaths.tsv, ro-crate-metadata.json and file_list.tsv (default: current directory).",
    )
    args = parser.parse_args()

    if urlparse(args.study_url).scheme not in ("http", "https"):
        print("Error: study_url must be an http(s) URL", file=sys.stderr)
        sys.exit(1)

    study, components = parse_study_txt(fetch_study_txt(args.study_url))

    if components:
        pairs = select_components(components)
    else:
        pairs = [(None, derive_fallback_container_name(study))]

    containers_info = []
    for component, container_name in pairs:
        matches = find_containers(container_name)
        if len(matches) != 1:
            print(
                f"Expected exactly one IDR project or screen named '{container_name}', "
                f"found {len(matches)}",
                file=sys.stderr,
            )
            sys.exit(1)

        container_type, matched = matches[0]
        container_id = matched["@id"]
        container = get_container(container_type, container_id).get("data", {})

        print(f"Found {container_type}: {container.get('Name', container_id)}")
        containers_info.append({
            "component": component,
            "container_type": container_type,
            "container_name": container_name,
            "container": container,
        })

    os.makedirs(args.output_dir, exist_ok=True)
    build_crate(study, containers_info, args.output_dir)
    write_filepaths_tsv(containers_info, args.output_dir)


if __name__ == "__main__":
    main()
