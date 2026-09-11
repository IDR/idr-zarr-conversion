import argparse
import json
from pathlib import Path

import dask
import ngff_zarr as nz
import zarr


def node_type(path: Path) -> str | None:
    metadata_path = path / "zarr.json"
    if not metadata_path.is_file():
        return None
    with metadata_path.open(encoding="utf-8") as metadata_file:
        return json.load(metadata_file).get("node_type")


def series_paths(input_path: Path) -> list[str]:
    children = sorted(
        (child for child in input_path.iterdir() if child.name.isdigit()),
        key=lambda child: int(child.name),
    )
    groups = [child for child in children if node_type(child) == "group"]
    if groups:
        return [f"/{group.name}/0" for group in groups]
    return ["/0"]


def group_attrs(path: Path) -> dict:
    metadata_path = path / "zarr.json"
    if metadata_path.is_file():
        with metadata_path.open(encoding="utf-8") as metadata_file:
            return json.load(metadata_file).get("attributes", {})
    with (path / ".zattrs").open(encoding="utf-8") as metadata_file:
        return json.load(metadata_file)


def pyramid_metadata(input_path: Path, array_path: str) -> tuple[list[str], dict[str, float], list[dict[str, int]]]:
    group_path = input_path.joinpath(*array_path.strip("/").split("/")[:-1])
    attrs = group_attrs(group_path)
    multiscales = attrs.get("ome", attrs).get("multiscales", [])
    if not multiscales:
        raise ValueError(f"No multiscales metadata found at {group_path}")

    metadata = multiscales[0]
    dims = [axis["name"] if isinstance(axis, dict) else axis for axis in metadata["axes"]]
    scales = []
    for dataset in metadata["datasets"]:
        transformation = next(
            (item for item in dataset.get("coordinateTransformations", []) if item["type"] == "scale"),
            None,
        )
        scales.append(transformation["scale"] if transformation else [1] * len(dims))

    pixel_size = dict(zip(dims, scales[0]))
    scale_factors = []
    for level_scale in scales[1:]:
        factors = {dim: value / pixel_size[dim] for dim, value in zip(dims, level_scale)}
        if "x" not in factors or "y" not in factors or "z" not in factors:
            raise ValueError(f"Expected x, y, and z axes at {group_path}")
        if not abs(factors["x"] - factors["y"]) < 1e-6:
            raise ValueError(f"X and Y scale factors differ at {group_path}")
        factor = round(factors["x"])
        if factor < 1 or not abs(factors["x"] - factor) < 1e-6:
            raise ValueError(f"Non-integral XY scale factor at {group_path}: {factors['x']}")
        scale_factors.append({"x": factor, "y": factor, "z": factor})
    return dims, pixel_size, scale_factors


def downsample(input_path: Path, array_path: str, output_path: Path) -> None:
    series_parts = array_path.strip("/").split("/")[:-1]
    series_path = input_path.joinpath(*series_parts)
    source = nz.from_ngff_zarr(series_path)
    image = source.images[0]
    dims, pixel_size, scale_factors = pyramid_metadata(input_path, array_path)
    chunks = tuple(image.data.chunksize)
    print(f"Series: {array_path}")
    print(f"  Dimensions: {dims}")
    print(f"  Shape: {image.data.shape}")
    print(f"  Chunks: {chunks}")
    print(f"  Pixel size: {pixel_size}")
    print(f"  Resolutions: {len(scale_factors) + 1}")
    print(f"  Downsampling factors: {scale_factors}")
    print(f"  Output: {output_path}")
    multiscales = nz.to_multiscales(image, scale_factors=scale_factors, chunks=chunks)
    nz.to_ngff_zarr(output_path, multiscales, version="0.5")


def main() -> None:
    parser = argparse.ArgumentParser(description="Downsample an OME-Zarr image.")
    parser.add_argument("input", type=Path, help="Path to the input OME-Zarr")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=Path("output"),
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help="Number of parallel Dask worker threads (default: Dask automatic)",
    )
    args = parser.parse_args()
    if args.workers is not None and args.workers < 1:
        parser.error("--workers must be at least 1")

    input_path = args.input
    output_path = args.output / input_path.name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths = series_paths(input_path)
    dask_config = {"scheduler": "threads"}
    if args.workers is not None:
        dask_config["num_workers"] = args.workers
    print(f"Workers: {args.workers or 'automatic'}")

    with dask.config.set(dask_config):
        if paths[0].count("/") > 1:
            zarr.open_group(output_path, mode="w-", zarr_format=3)
            for array_path in paths:
                downsample(input_path, array_path, output_path / array_path.split("/")[1])
        else:
            downsample(input_path, paths[0], output_path)


if __name__ == "__main__":
    main()
