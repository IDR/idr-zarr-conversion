# idr-zarr-conversion


## Setup

There are currently two VMs configured for the conversion work:

```
ssh -J idr-pilot.openmicroscopy.org rocky@pilot-idrconv
```
and
```
ssh -J idr-pilot.openmicroscopy.org rocky@pilot-idrconv2
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

## Zarr Conversion

Run `./convert.sh` to batch-convert image files to OME-Zarr:

```
./convert.sh [--workers N] [--id ID] <input_file>
```

- **`<input_file>`** — path to a tab-separated file (see format below)
- **`--id ID`** — output identifier, e.g. `idr0027-dickerson-chromatin/experimentA` (prompted if not provided); files are written to `/data/output/<ID>/`
- **`--workers N`** — number of bioformats2raw worker threads (default: `14`)

### Input TSV format

The input file is a two-column, tab-separated file with no header and linebreak at last line:

| Column | Description |
|--------|-------------|
| `target_dir` | Subdirectory name under `/data/output/<ID>/` where the output `.ome.zarr` will be placed |
| `filepath` | Absolute path to the source image file |

`target_dir` typically will be the Dataset names.

Create this input.tsv from the filePaths.tsv (resp. plates.tsv) of the IDR project.

Example:

```
TreatStartDay3_mouse50	/uod/idr/filesets/idr0026-weigelin-immunotherapy/.../Pos00.tif
TreatStartDay3_mouse50	/uod/idr/filesets/idr0026-weigelin-immunotherapy/.../Pos01.tif
TreatStartDay3_mouse55	/uod/idr/filesets/idr0026-weigelin-immunotherapy/.../Pos00.tif
```
### Check

Change into the output directory, e.g.:
`cd /data/output/idr0027-dickerson-chromatin`

Brief check if all ome.zarr were created:
`find * -type d -name "*.ome.zarr" | wc -l`

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

## RO-Crate

Run:
```
mm activate crate
python crate.py idr0027-dickerson-chromatin/experimentA /data/output
```

The first argument must exactly match one full IDR container name. It is also used as the directory structure; for example, `idr0027-dickerson-chromatin/experimentA` writes to `/data/output/idr0027-dickerson-chromatin/experimentA/`.

Then run:
```
python update_sizes.py /data/output/idr0027-dickerson-chromatin/experimentA/file_list.tsv
```
This will update the file sizes in the tsv. 

Check:
```
bia-ro-crate validate /data/output/idr0027-dickerson-chromatin/experimentA
```
