# BIA RO-Crate Editor

A local web editor for building a BioImage Archive (BIA) RO-Crate from an IDR
study. It fetches study metadata and image names from the IDR OMERO JSON API,
lets you add and assign `ImageAcquisitionProtocol`,
`SpecimenImagingPreparationProtocol`, `SignalChannel` and `BioSample` entities,
then downloads a `ro-crate-metadata.json` and a `file_list.tsv`.

## Quick start

1. Create and activate a virtual environment inside this directory:

```bash
cd ro-crate-editor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Start the local server:

```bash
python server.py
```

3. Open `http://localhost:8000` in your browser.

## Usage

1. **Load a study** — paste an IDR URL such as
   `https://idr.openmicroscopy.org/webclient/?show=project-151`, or the exact
   IDR study name, and click *Load study*.
2. **Review Study metadata** — edit the pre-filled Study fields if needed.
3. **Add protocols** — create one or more `ImageAcquisitionProtocol` and
   `SpecimenImagingPreparationProtocol` entities. Signal channels can be added
   inside a specimen imaging preparation protocol.
4. **Add BioSamples** — the first BioSample is created from the IDR `Organism`
   annotation. Use *Look up NCBI taxon* to fill the Taxon automatically, or
   enter it manually. Add more BioSamples if the study uses more than one
   organism.
5. **Assign to datasets** — use the table to assign the correct BioSample,
   image acquisition protocol and specimen preparation protocol to each
   `bia:Dataset`.
6. **Review the file list** — it is generated automatically: one row per image
   for project datasets and one row per plate for screens.
7. **Export / verify** — download `ro-crate-metadata.json` and `file_list.tsv`, or run the bia-ro-crate validator against the current draft.

## Validation

After converting the images to OME-Zarr, run the BIA validator and update file
sizes:

```bash
bia-ro-crate validate /path/to/output
python ../scripts/update_sizes.py /path/to/output/file_list.tsv
```

## Files

- `server.py` — local HTTP server and IDR/NCBI proxy.
- `idr_client.py` — helpers for the IDR OMERO JSON API and NCBI taxonomy.
- `index.html`, `editor.js`, `style.css` — the web editor UI.
- `bia_context.json` — the BIA JSON-LD context used in the exported crate.
- `requirements.txt` — Python dependencies (only `requests` at present).
