# idr-zarr-conversion


## Setup

There's currently one VM configured for the conversion work:

```
ssh -J idr-pilot.openmicroscopy.org rocky@pilot-idrconv
```

EBI NFS mounted as usual: `/nfs/bioimage` and linked to `/uod/idr/filesets`.

idr-metadata cloned into: `/data/idr-metadata`.

10Tb data partition for temp ome.zarr storage: `/data`.
  - `/data/output` - output location
  - `/data/memo` - temporary memo directory for bioformats2raw
  - `/data/input` - input location (if needed, use `/nfs/bioimage` instead when possible)

bioformats2raw (0.12.0) and bftools (8.5.0) installed too 
(ie. `bioformats2raw`, `showinf` available on commandline)

## Conversion

Run `scripts/convert.sh` to batch-convert image files to OME-Zarr:

```
./scripts/convert.sh [--workers N] [--id ID] <input_file>
```

- **`<input_file>`** — path to a tab-separated file (see format below)
- **`--id ID`** — output identifier, e.g. `idr0026` (prompted if not provided); files are written to `/data/output/<ID>/`
- **`--workers N`** — number of bioformats2raw worker threads (default: `14`)

### Input TSV format

The input file is a two-column, tab-separated file with no header:

| Column | Description |
|--------|-------------|
| `target_dir` | Subdirectory name under `/data/output/<ID>/` where the output `.ome.zarr` will be placed |
| `filepath` | Absolute path to the source image file |

Example:

```
TreatStartDay3_mouse50	/uod/idr/filesets/idr0026-weigelin-immunotherapy/.../Pos00.tif
TreatStartDay3_mouse50	/uod/idr/filesets/idr0026-weigelin-immunotherapy/.../Pos01.tif
TreatStartDay3_mouse55	/uod/idr/filesets/idr0026-weigelin-immunotherapy/.../Pos00.tif
```
### Check

Brief check if all ome.zarr were created:
`find * -type d -name "*.ome.zarr" | wc -l`

Should match:
`wc -l input.tsv`
