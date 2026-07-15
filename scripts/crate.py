import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

BASE_URL = "https://idr.openmicroscopy.org/api/v0"
WEBCLIENT = "https://idr.openmicroscopy.org/webclient"

BIA_CONTEXT = {
    "@type": {"@container": "@set"},
    "accessionId": {"@id": "bia:accessionId"},
    "acknowledgement": {"@id": "bia:acknowledgement"},
    "additionalType": {"@id": "schema:additionalType"},
    "address": {"@id": "schema:address"},
    "associatedAnnotationMethod": {"@id": "bia:associatedAnnotationMethod", "@container": "@set"},
    "associatedBiologicalEntity": {"@id": "bia:associatedBiologicalEntity", "@container": "@set"},
    "associatedCreationProcess": {"@id": "bia:associatedCreationProcess"},
    "associatedImageAcquisitionProtocol": {"@id": "bia:associatedImageAcquisitionProtocol", "@container": "@set"},
    "associatedImageAnalysisMethod": {"@id": "bia:associatedAnalysisMethod", "@container": "@set"},
    "associatedImageCorrelationMethod": {"@id": "bia:associatedCorrelationMethod", "@container": "@set"},
    "associatedProtocol": {"@id": "bia:associatedProtocol", "@container": "@set"},
    "associatedSourceImage": {"@id": "bia:associatedSourceImage", "@container": "@set"},
    "associatedSpecimen": {"@id": "bia:associatedSubject"},
    "associatedSpecimenImagingPreparationProtocol": {"@id": "bia:associatedImagingPreparationProtocol", "@container": "@set"},
    "author": {"@id": "schema:author", "@container": "@set"},
    "authorNames": {"@id": "bia:authorNames"},
    "bia": "http://bia/",
    "biologicalEntity": {"@id": "bia:sampleOf", "@container": "@set"},
    "datePublished": {"@id": "schema:datePublished"},
    "dc": "http://purl.org/dc/terms/",
    "description": {"@id": "schema:description"},
    "doi": {"@id": "bia:doi"},
    "email": {"@id": "schema:email"},
    "experimentalVariableDescription": {"@id": "bia:experimentalVariableDescription", "@container": "@set"},
    "extrinsicVariableDescription": {"@id": "bia:extrinsicVariableDescription", "@container": "@set"},
    "fbbiId": {"@id": "bia:fbbiId", "@container": "@set"},
    "funder": {"@id": "schema:funder", "@container": "@set"},
    "funding": {"@id": "schema:funding", "@container": "@set"},
    "growthProtocol": {"@id": "bia:growthProtocol"},
    "hasPart": {"@id": "schema:hasPart", "@container": "@set"},
    "identifier": {"@id": "schema:identifier"},
    "imagingInstrumentDescription": {"@id": "bia:imagingInstrumentDescription"},
    "imagingMethodName": {"@id": "bia:imagingMethodName", "@container": "@set"},
    "intrinsicVariableDescription": {"@id": "bia:intrinsicVariableDescription", "@container": "@set"},
    "keywords": {"@id": "schema:keywords", "@container": "@set"},
    "license": {"@id": "schema:license"},
    "memberOf": {"@id": "schema:memberOf", "@container": "@set"},
    "name": {"@id": "schema:name"},
    "organismClassification": {"@id": "schema:taxonomicRange", "@container": "@set"},
    "pubmedId": {"@id": "bia:pubmedId"},
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "relatedPublication": {"@id": "bia:relatedPublication", "@container": "@set"},
    "role": {"@id": "bia:role", "@container": "@set"},
    "schema": "http://schema.org/",
    "commonName": {"@id": "http://rs.tdwg.org/dwc/terms/vernacularName"},
    "scientificName": {"@id": "http://rs.tdwg.org/dwc/terms/scientificName"},
    "seeAlso": {"@id": "http://www.w3.org/2000/01/rdf-schema#seeAlso", "@container": "@set"},
    "website": {"@id": "bia:website"},
    "yearPublished": {"@id": "bia:yearPublished"},
    "csvw": "http://www.w3.org/ns/csvw#",
    "tableSchema": {"@id": "csvw:tableSchema"},
    "column": {"@id": "csvw:column", "@container": "@set"},
    "columnName": {"@id": "csvw:name"},
    "propertyUrl": {"@id": "csvw:propertyUrl"},
}


def idr_get(path: str, **params) -> dict:
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def zarr_name(client_path: str) -> str:
    return Path(client_path).stem + ".ome.zarr"


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
                result[kv[0]] = kv[1]
    return result


def find_containers(study_id: str) -> list:
    matches = []
    for screen in idr_get("/m/screens/").get("data", []):
        if screen["Name"] == study_id:
            matches.append(("screen", screen))
    for project in idr_get("/m/projects/").get("data", []):
        if project["Name"] == study_id:
            matches.append(("project", project))
    return matches


def get_image_path(image_id: int) -> str:
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


def get_images(container_type: str, child_id: int) -> list:
    if container_type == "screen":
        wells = idr_get(f"/m/plates/{child_id}/wells/").get("data", [])
        images = []
        for well in wells:
            for ws in well.get("WellSamples", []):
                images.append(ws["Image"])
        return images
    return idr_get(f"/m/datasets/{child_id}/images/").get("data", [])


def extract_url(value: str) -> str:
    """Extract URL from strings like 'CC BY 4.0 https://...' or bare URLs."""
    m = re.search(r"https?://\S+", value)
    return m.group(0).rstrip(".") if m else value


def build_authors(authors_str: str) -> list:
    """Turn 'Last F, Last2 F2, ...' into a list of author entity dicts."""
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


def build_crate(container_type: str, container: dict, output_dir: str):
    container_id = container["@id"]
    ann = get_annotations(container_type, container_id)

    license_url = extract_url(ann.get("License", "https://creativecommons.org/licenses/by/4.0/"))
    release_date = ann.get("Release Date", "")
    pub_doi_raw = ann.get("Publication DOI", "")
    pub_doi_url = extract_url(pub_doi_raw) if pub_doi_raw else None
    data_doi_raw = ann.get("Data DOI", "")
    data_doi_url = extract_url(data_doi_raw) if data_doi_raw else None
    pubmed_raw = ann.get("PubMed ID", "")
    pubmed_id = pubmed_raw.split()[0] if pubmed_raw else None
    imaging_method = ann.get("Imaging Method", "")
    organism = ann.get("Organism", "")
    study_type = ann.get("Study Type", "")
    authors_str = ann.get("Publication Authors", "")
    pub_title = ann.get("Publication Title", "")

    author_entities = build_authors(authors_str)
    author_refs = [{"@id": a["@id"]} for a in author_entities]

    graph = []

    graph.append({
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
        "about": {"@id": "./"},
    })

    has_part = []
    dataset_entities = []
    image_entities = []

    if container_type == "screen":
        children = idr_get(f"/m/screens/{container_id}/plates/").get("data", [])
    else:
        children = idr_get(f"/m/projects/{container_id}/datasets/").get("data", [])

    for child in children:
        child_id = child["@id"]
        dataset_id = f"#Dataset-{child_id}"
        dataset_images = []

        dataset_name = child["Name"]
        for image in get_images(container_type, child_id):
            image_id = image["@id"]
            client_path = get_image_path(image_id)
            zarr = zarr_name(client_path)
            zarr_path = f"{dataset_name}/{zarr}"
            entity_id = f"#Image-{image_id}"
            image_entity = {
                "@id": entity_id,
                "@type": ["File", "bia:Image"],
                "name": zarr,
                "description": image.get("Description") or "",
                "memberOf": [{"@id": dataset_id}],
                "_zarr_path": zarr_path,
            }
            image_entities.append(image_entity)
            dataset_images.append({"@id": entity_id})
            has_part.append({"@id": entity_id})

        dataset_entity = {
            "@id": dataset_id,
            "@type": ["Dataset", "bia:Dataset"],
            "name": child["Name"],
            "description": child.get("Description") or "",
            "hasPart": dataset_images,
            "associatedBiologicalEntity": [{"@id": "#Biosample-1"}] if organism else [],
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
        has_part.insert(0, {"@id": dataset_id})

    root_entity = {
        "@id": "./",
        "@type": ["Dataset", "bia:Study"],
        "name": container["Name"],
        "description": container.get("Description") or "",
        "license": license_url,
        "datePublished": release_date,
        "author": author_refs,
        "keywords": [study_type] if study_type else [],
        "acknowledgement": None,
        "hasPart": has_part,
        "accessionId": container["Name"].split("/")[0].split("-")[0].upper() if "/" in container["Name"] else "",
        "relatedPublication": [{"@id": pub_doi_url}] if pub_doi_url else [],
        "doi": data_doi_url,
        "funding": [],
        "seeAlso": [],
    }
    if pubmed_id:
        root_entity["pubmedId"] = pubmed_id
    if imaging_method:
        root_entity["imagingMethodName"] = [imaging_method]

    graph.append(root_entity)

    if pub_doi_url:
        graph.append({
            "@id": pub_doi_url,
            "@type": "bia:Publication",
            "name": pub_title,
            "doi": pub_doi_url,
            "pubmedId": pubmed_id,
            "authorNames": authors_str,
        })

    if organism:
        graph.append({
            "@id": "#organism-1",
            "@type": "bia:Taxon",
            "scientificName": organism,
            "commonName": None,
        })
        graph.append({
            "@id": "#Biosample-1",
            "@type": "bia:BioSample",
            "name": organism,
            "description": organism,
            "experimentalVariableDescription": [],
            "extrinsicVariableDescription": [],
            "intrinsicVariableDescription": [],
            "organismClassification": [{"@id": "#organism-1"}],
            "growthProtocol": None,
        })

    file_list_name = "file_list.tsv"
    file_list_rows = []
    for img in image_entities:
        file_list_rows.append({
            "path": img.pop("_zarr_path"),
            "size": "",
            "dataset": img["memberOf"][0]["@id"],
            "biostudies_type": "file",
        })

    graph.append({
        "@id": "_:ts0",
        "@type": ["csvw:Schema"],
        "column": [
            {"@id": "_:col0"},
            {"@id": "_:col1"},
            {"@id": "_:col2"},
            {"@id": "_:col3"},
        ],
    })
    graph.append({"@id": "_:col0", "@type": ["csvw:Column"], "columnName": "path", "propertyUrl": "http://bia/filePath"})
    graph.append({"@id": "_:col1", "@type": ["csvw:Column"], "columnName": "size", "propertyUrl": "http://bia/sizeInBytes"})
    graph.append({"@id": "_:col2", "@type": ["csvw:Column"], "columnName": "dataset", "propertyUrl": "http://schema.org/isPartOf"})
    graph.append({"@id": "_:col3", "@type": ["csvw:Column"], "columnName": "biostudies_type", "propertyUrl": None})
    graph.append({
        "@id": file_list_name,
        "@type": ["File", "bia:FileList", "csvw:Table"],
        "tableSchema": {"@id": "_:ts0"},
    })
    has_part.append({"@id": file_list_name})

    graph.extend(author_entities)
    graph.extend(dataset_entities)
    graph.extend(image_entities)

    crate_doc = {
        "@context": ["https://w3id.org/ro/crate/1.1/context", BIA_CONTEXT],
        "@graph": graph,
    }

    os.makedirs(output_dir, exist_ok=True)

    tsv_path = os.path.join(output_dir, file_list_name)
    with open(tsv_path, "w", encoding="utf-8") as f:
        f.write("path\tsize\tdataset\tbiostudies_type\n")
        for row in file_list_rows:
            f.write(f"{row['path']}\t{row['size']}\t{row['dataset']}\t{row['biostudies_type']}\n")

    out_path = os.path.join(output_dir, "ro-crate-metadata.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(crate_doc, f, indent=4, ensure_ascii=False)
    print(f"RO-Crate written to {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a BIA-compliant RO-Crate from an IDR study."
    )
    parser.add_argument(
        "study_id",
        help="Exact full container name, e.g. idr0027-dickerson-chromatin/experimentA",
    )
    parser.add_argument("output_dir", help="Base output directory, e.g. /data/output")
    args = parser.parse_args()

    matches = find_containers(args.study_id)
    if len(matches) != 1:
        print(
            f"Expected exactly one screen or project named '{args.study_id}', "
            f"found {len(matches)}",
            file=sys.stderr,
        )
        sys.exit(1)
    container_type, container = matches[0]
    print(f"Found {container_type}: {container['Name']}")
    output_path = os.path.join(args.output_dir, container["Name"])
    build_crate(container_type, container, output_path)


if __name__ == "__main__":
    main()
