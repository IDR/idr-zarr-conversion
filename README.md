# idr-zarr-conversion


## Setup

There are currently two VMs configured for the conversion work:

```
ssh -J rocky@idr-pilot.openmicroscopy.org rocky@pilot-idrconv
```
and
```
ssh -J rocky@idr-pilot.openmicroscopy.org rocky@pilot-idrconv2
```

EBI NFS mounted as usual: `/nfs/bioimage` and linked to `/uod/idr/filesets`.

idr-metadata cloned into: `/data/idr-metadata`.

10Tb data partition for temp ome.zarr storage: `/data`.
  - `/data/output` - output location
  - `/data/memo` - temporary memo directory for bioformats2raw
  - `/data/input` - input location (if needed, use `/nfs/bioimage` instead when possible)

bioformats2raw (0.12.0) and bftools (8.5.0) installed too 
(ie. `bioformats2raw`, `showinf` available on commandline)

All necessary scripts are in the home directory of the rocky user under `idr-zarr-conversion/scripts`.

## Preparation

Generate `filepaths.tsv` (for `convert.sh` below), `ro-crate-metadata.json` and `file_list.tsv` from an IDR `study.txt` URL:

```
mm activate crate
python scripts/metadata.py https://raw.githubusercontent.com/IDR/idr-metadata/master/idr0027-dickerson-chromatin/idr0027-study.txt --output-dir /data/output/idr0027-dickerson-chromatin
```

The script downloads the IDR study metadata and, for every experiment/screen component declared in `study.txt` (a study may have several, e.g. idr0038 has experimentA/B/C each backed by its own OMERO project/screen), derives the OMERO project/screen name and fetches its image or plate list from the IDR JSON API. All components are written to a single set of output files in `--output-dir`:

- `filepaths.tsv` — a 3-column, no-header TSV used as the input for `convert.sh`. Column 1 is rooted at the study accession, e.g. `<study>/experiment<A/B/...>/<Dataset name>` for projects or `<study>/screen<A/B/...>` for screens — this is a path relative to `/data/output`, so `convert.sh` needs no separate output identifier. Column 2 is the real source filesystem path (leading `/` added if missing, taken from the image's `client_path`), and column 3 is `<file name>.ome.zarr` (derived from the source file name, **not** the OMERO image/plate name). Rows are deduplicated, since more than one OMERO image can point at the same underlying file.
- `ro-crate-metadata.json` and `file_list.tsv` — a minimal BIA RO-Crate. `file_list.tsv` contains three columns: `file_path`, `dataset`, and `type`, where `file_path` is relative to the RO-Crate root (i.e. `--output-dir`, without the study accession), e.g. `experiment<A/B/...>/<dataset>/<image>.ome.zarr` for projects or `screen<A/B/...>/<plate>.ome.zarr` for screens.

Check:
```
bia-ro-crate validate /data/output/idr0027-dickerson-chromatin
```

## Zarr Conversion

Run `./convert.sh` to batch-convert image files to OME-Zarr, using the `filepaths.tsv` generated above:

```
./convert.sh [--workers N] <input_file>
```

- **`<input_file>`** — path to `filepaths.tsv` (see format below)
- **`--workers N`** — number of bioformats2raw worker threads (default: `14`)

Since `filepaths.tsv`'s first column already includes the study accession, files are written directly under `/data/output/<column 1>/` — no separate output identifier is needed.

### Input TSV format

The input file is `filepaths.tsv` as written by `scripts/metadata.py`: a three-column, tab-separated file with no header — target subdirectory (rooted at the study accession), source file path, and `.ome.zarr` output name.

Example:

```
idr0027-dickerson-chromatin/experimentA/Genomic separation 25kb	/uod/idr/filesets/idr0027-dickerson-chromatin/20160719/RawVideos/25kb/a_upper_b_lower.dv	a_upper_b_lower.ome.zarr
idr0027-dickerson-chromatin/experimentA/Genomic separation 25kb	/uod/idr/filesets/idr0027-dickerson-chromatin/20160719/RawVideos/25kb/c_upper_d_lower.dv	c_upper_d_lower.ome.zarr
idr0027-dickerson-chromatin/experimentA/Genomic separation 25kb	/uod/idr/filesets/idr0027-dickerson-chromatin/20160719/RawVideos/25kb/e_upper.dv	e_upper.ome.zarr
...
```

```
./convert.sh /data/output/idr0027-dickerson-chromatin/filepaths.tsv
```

Will create ome.zarrs in:
```
/data/output/idr0027-dickerson-chromatin/experimentA/Genomic separation 25kb/a_upper_b_lower.ome.zarr
/data/output/idr0027-dickerson-chromatin/experimentA/Genomic separation 25kb/c_upper_d_lower.ome.zarr
/data/output/idr0027-dickerson-chromatin/experimentA/Genomic separation 25kb/e_upper.ome.zarr
...
```


### Check

Change into the output directory, e.g.:
`cd /data/output/idr0027-dickerson-chromatin`

Brief check if all ome.zarr were created:
`find * -type d -name "*.ome.zarr" | wc -l`

#### Check with validator

SSH with port forward:
`ssh -J rocky@idr-pilot.openmicroscopy.org rocky@pilot-idrconv -L 8000:localhost:8000`

Then
`mm run -n crate ome_zarr view XYZ.ome.zarr` 

Then go to
`https://ome.github.io/ome-ngff-validator/?source=http://localhost:8000/XYZ.ome.zarr/0`

## Zip

Change into the output directory, e.g.:
`cd /data/output/idr0027-dickerson-chromatin`

```
find . -type d -name "*.ome.zarr" -exec sh -c '
for d; do
    parent=$(dirname "$d")
    base=$(basename "$d")
    (
        cd "$parent" &&
        zip -0 -r "${base}.zip" "$base"
    )
done
' sh {} +
```
(just copy and paste the whole block into terminal)

Check if all zarrs have been zipped:
`find * -type f -name "*.zip" | wc -l`

Then delete the zarrs:
`find * -type d -name "*.ome.zarr" -exec rm -rf {} \;`
(Make sure you are in the correct study directory, e.g. `/data/output/idr0027-dickerson-chromatin`!)

In case you need to unzip again:
```
find . -type f -name "*.zip" -exec sh -c '
for d; do
    parent=$(dirname "$d")
    base=$(basename "$d")
    (
        cd "$parent" &&
        unzip "$base"
    )
done
' sh {} +
```

## Submit

**Note: This will change again, when BIA has a proper submission system**

Start globuspersonalconnect in a separate screen:
```
screen -dmS globus /home/rocky/globusconnectpersonal-3.2.9/globusconnectpersonal -start
```

Start transfer (doesn't need screen, just triggers the transfer)
```
globus transfer "e59ed979-80f9-11f1-b195-0ee7ef9370d9:/data/output/idr0027-dickerson-chromatin" "7d3add40-d193-473e-b066-138c1ee54e3e:/" --recursive --label "idr0027"
```

(Note: `7d3add40-d193-473e-b066-138c1ee54e3e` is the ID for the "IDR_ome_zarr" collection on BIA side,
`e59ed979-80f9-11f1-b195-0ee7ef9370d9` is the ID for the local endpoint (you can get it with `globus endpoint local-id`))

Globus CLI was installed via
```
mm env create -n globus python=3.13
mm activate globus
pip install globus-cli
```

It might be necessary to login again, in which case:
```
mm activate globus
globus login
```
