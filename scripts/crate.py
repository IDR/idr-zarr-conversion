#!/usr/bin/env python3
"""Generate a minimal BIA RO-Crate from an IDR study.txt URL.

The script downloads an IDR study.txt file, parses its tab-delimited metadata,
derives the OMERO project/screen name, fetches the image/plate list from the
IDR OMERO JSON API, and writes a minimal ro-crate-metadata.json plus a
file_list.tsv with 1024-byte placeholder file sizes.
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

# Make the ro-crate-editor helper modules importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ro-crate-editor"))

from idr_client import (  # noqa: E402
    find_containers,
    get_child_files,
    get_children,
    get_container,
    ncbi_taxon,
)

BIA_CONTEXT_PATH = REPO_ROOT / "ro-crate-editor" / "bia_context.json"
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


def derive_container_name(
    study: dict, components: list, requested: str | None = None
) -> str:
    """Derive the OMERO project/screen name from study.txt metadata."""
    if requested:
        for c in components:
            name = component_name(c)
            if name and requested in name:
                return name
        raise ValueError(
            f"Component '{requested}' not found in study.txt. "
            f"Available: {[n for n in (component_name(c) for c in components) if n]}"
        )

    if len(components) == 1:
        name = component_name(components[0])
        if name:
            return name

    if len(components) > 1:
        names = [component_name(c) for c in components]
        raise ValueError(
            f"Study has multiple components: {names}. "
            "Use --component-name to select one."
        )

    # Fall back to top-level keys.
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


def derive_accession_id(container_name: str) -> str:
    """Turn 'idr0027-dickerson-chromatin/experimentA' into 'IDR0027A'."""
    parts = container_name.split("/")
    base = parts[0].split("-")[0].upper()
    if len(parts) < 2:
        return base
    suffix_part = parts[1].strip()
    letter_match = re.search(r"[A-Za-z](?=[^A-Za-z]*$)", suffix_part)
    suffix = (
        letter_match.group(0).upper()
        if letter_match
        else suffix_part[-1:].upper()
    )
    return base + suffix


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


def build_crate(
    study: dict,
    component: dict | None,
    container_type: str,
    container: dict,
    output_dir: str,
):
    container_id = container["@id"]
    container_name = container.get("Name", "")
    container_description = container.get("Description", "")

    comp = component.get("data", {}) if component else {}
    is_screen = component.get("type") == "screen" if component else False

    title = (
        comp.get("Screen Title" if is_screen else "Experiment Title", "").strip()
        or study.get("Study Title", "").strip()
        or container_name
    )
    description = (
        comp.get("Screen Description" if is_screen else "Experiment Description", "").strip()
        or study.get("Study Description", "").strip()
        or container_description
    )
    organism = (
        comp.get("Screen Organism" if is_screen else "Experiment Organism", "").strip()
        or study.get("Study Organism", "").strip()
    )
    study_type = (
        comp.get("Screen Imaging Method" if is_screen else "Experiment Imaging Method", "").strip()
        or study.get("Study Type", "").strip()
    )
    keywords = [k.strip() for k in study.get("Study Key Words", "").split(",") if k.strip()]
    if study_type and study_type not in keywords:
        keywords.insert(0, study_type)

    license_url = extract_url(study.get("Study License URL", study.get("Study License", ""))) or DEFAULT_LICENSE
    release_date = study.get("Study Public Release Date", "").strip()
    data_doi = (
        extract_url(comp.get("Screen Data DOI" if is_screen else "Experiment Data DOI", "").strip())
        or extract_url(study.get("Study Data DOI", "").strip())
        or None
    )
    pub_doi = extract_url(study.get("Study DOI", "").strip()) or None
    pubmed_id = study.get("Study PubMed ID", "").strip() or None
    pub_title = study.get("Study Publication Title", "").strip()
    authors_str = study.get("Study Author List", "").strip()

    author_entities = build_authors(authors_str)
    author_refs = [{"@id": a["@id"]} for a in author_entities]
    accession_id = derive_accession_id(container_name)

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

    # Datasets / plates
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

        for f in get_child_files(container_type, child_id, child_name):
            file_list_rows.append({
                "file_path": f["path"],
                "dataset": ds_id,
                "type": "bia:Image",
            })

    # Root Study entity
    root_entity = {
        "@id": "./",
        "@type": ["Dataset", "bia:Study"],
        "name": container_name or title,
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
    if study_type:
        root_entity["imagingMethodName"] = [study_type]

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


def main():
    parser = argparse.ArgumentParser(
        description="Generate a minimal BIA RO-Crate from an IDR study.txt URL."
    )
    parser.add_argument(
        "study_url",
        help="URL of the IDR study.txt file (e.g. a GitHub raw URL).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=".",
        help="Directory to write ro-crate-metadata.json and file_list.tsv (default: current directory).",
    )
    parser.add_argument(
        "--component-name",
        "-c",
        default=None,
        help="Select a specific experiment/screen when the study has multiple components.",
    )
    args = parser.parse_args()

    if urlparse(args.study_url).scheme not in ("http", "https"):
        print("Error: study_url must be an http(s) URL", file=sys.stderr)
        sys.exit(1)

    study, components = parse_study_txt(fetch_study_txt(args.study_url))
    container_name = derive_container_name(study, components, args.component_name)

    # Find the component matching the selected container name.
    selected_component = None
    for c in components:
        if component_name(c) == container_name:
            selected_component = c
            break

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
    build_crate(study, selected_component, container_type, container, args.output_dir)


if __name__ == "__main__":
    main()
