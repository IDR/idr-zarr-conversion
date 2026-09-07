#!/usr/bin/env python3
"""Derive bioformats2raw chunk/shard parameters from a microscopy image.

Runs ``showinf -nopix`` on the supplied image, parses the core metadata for
image dimensions and pixel type, selects the series with the largest 3D
volume, and prints the raw bioformats2raw flags needed for tile/shard sizes.

The tile/shard dimensions are derived in ``derive_parameters``.

Info is printed to STDERR and paramaters to STDOUT, so can be captured like
params=`python bfparams.py PATH` for script usage.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


SHOWINF = "showinf"

# Target sizes for a single chunk and a single shard.
TARGET_CHUNK_SIZE = 768 * 1024  # ~768 KiB
TARGET_SHARD_SIZE = 10 * 1024 * 1024  # ~10 MiB


def run_showinf(image_path: str) -> str:
    """Run ``showinf -nopix`` and return its stdout as a string."""
    result = subprocess.run(
        [SHOWINF, "-nopix", image_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"showinf failed (exit {result.returncode}): {stderr}")
    return result.stdout


def parse_core_metadata(text: str) -> list[dict]:
    """Parse core metadata into a list of per-series dimension dicts.

    Each returned dict contains integer keys ``Width``, ``Height``,
    ``SizeZ``, ``SizeT``, ``SizeC`` and the string key ``Pixel type``.
    Missing numeric values default to 1.
    """
    start = text.find("Reading core metadata")
    end = text.find("Reading global metadata")
    core = text[start:end] if start != -1 and end != -1 else text[start:]

    series_list: list[dict] = []
    current: dict | None = None

    for line in core.splitlines():
        if re.match(r"^\s*Series\s+#\d+\s*:\s*$", line):
            if current is not None:
                series_list.append(current)
            current = {}
            continue

        if current is None:
            continue

        m = re.match(
            r"^\s*(Width|Height|SizeZ|SizeT|SizeC|Pixel type)\s*=\s*(.+?)\s*$",
            line,
        )
        if not m:
            continue

        key, value = m.group(1), m.group(2)
        if key == "Pixel type":
            current[key] = value
        else:
            try:
                current[key] = int(value)
            except ValueError:
                current[key] = 1

    if current is not None:
        series_list.append(current)

    if not series_list and core:
        # Some outputs omit per-series headers for a single series.
        fallback: dict = {}
        for line in core.splitlines():
            m = re.match(
                r"^\s*(Width|Height|SizeZ|SizeT|SizeC|Pixel type)\s*=\s*(.+?)\s*$",
                line,
            )
            if not m:
                continue
            key, value = m.group(1), m.group(2)
            if key == "Pixel type":
                fallback[key] = value
            else:
                try:
                    fallback[key] = int(value)
                except ValueError:
                    fallback[key] = 1
        if fallback:
            series_list.append(fallback)

    return series_list


def series_volume(series: dict) -> int:
    """Return Width * Height * SizeZ for a parsed series."""
    return (
        series.get("Width", 1)
        * series.get("Height", 1)
        * series.get("SizeZ", 1)
    )


def choose_series(series_list: list[dict]) -> dict:
    """Return the series with the largest 3D volume (first on ties)."""
    return max(series_list, key=series_volume)


def bytes_per_pixel(pixel_type: str) -> int:
    """Return bytes per pixel for a Bio-Formats pixel type string."""
    mapping = {
        "uint8": 1,
        "int8": 1,
        "uint16": 2,
        "int16": 2,
        "uint32": 4,
        "int32": 4,
        "float": 4,
        "double": 8,
    }
    pt = pixel_type.strip().lower()
    if pt in mapping:
        return mapping[pt]

    # Fallback: try to infer from a bit-width suffix like uint12, int12.
    m = re.search(r"(\d+)", pt)
    if m:
        bits = int(m.group(1))
        return max(1, bits // 8)
    return 1


def derive_parameters(
    width: int,
    height: int,
    size_z: int,
    pixel_type: str,
) -> tuple[int, int, int, int, int, int, int, int]:
    """Return chunk and shard dimensions for bioformats2raw.

    Parameters are returned as ``(chunk_w, chunk_h, chunk_z,
    shard_w, shard_h, shard_depth, chunk_size, shard_size)``.
    """
    bpp = bytes_per_pixel(pixel_type)
    shard_factor = 2

    # Calculate chunk sizes for 1mb chunk assuming 1 byte-per-pixel.
    if size_z < 26: # XY plane image with a few Z planes, try to keep z=1
        chunk_w = min(1024, width)
        chunk_h = min(1024, height)
        # target chunk_z size to ensure >= 1mb chunk
        chunk_z = (1048576 + chunk_w * chunk_h - 1) // (chunk_w * chunk_h) # round up
        # but it can't be larger than size_z
        chunk_z = min(chunk_z, size_z)
    else: # Truely XYZ volume, use 16 z slices
        chunk_w = min(256, width)
        chunk_h = min(256, height)
        chunk_z = min(16, size_z)

    # Scale chunk_z by "bytes per pixel"
    chunk_z = (chunk_z + bpp - 1) // bpp # round up

    # -> Should give a chunk size of 1Mb uncompressed bytes.
    chunk_size = chunk_w * chunk_h * chunk_z * bpp

    shard_w = chunk_w * shard_factor
    shard_h = chunk_h * shard_factor
    shard_depth = chunk_z * shard_factor if size_z > 1 else chunk_z
    shard_size = shard_w * shard_h * shard_depth * bpp

    return chunk_w, chunk_h, chunk_z, shard_w, shard_h, shard_depth, chunk_size, shard_size


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print bioformats2raw chunk/shard flags for an image.",
    )
    parser.add_argument("image", help="Path to the microscopy image file")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"Error: file not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    try:
        showinf_output = run_showinf(str(image_path))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    series_list = parse_core_metadata(showinf_output)
    if not series_list:
        print("Error: could not parse image dimensions from showinf output", file=sys.stderr)
        sys.exit(1)

    series = choose_series(series_list)
    width = series.get("Width", 1)
    height = series.get("Height", 1)
    size_z = series.get("SizeZ", 1)
    size_t = series.get("SizeT", 1)
    size_c = series.get("SizeC", 1)
    pixel_type = series.get("Pixel type", "")

    (
        chunk_w,
        chunk_h,
        chunk_z,
        shard_w,
        shard_h,
        shard_depth,
        chunk_size,
        shard_size,
    ) = derive_parameters(width, height, size_z, pixel_type)

    print(
        f"Image: Width={width} Height={height} SizeZ={size_z} "
        f"SizeT={size_t} SizeC={size_c} PixelType={pixel_type}\n"
        f"bioformats2raw parameters:",
        file=sys.stderr,
    )
    sys.stderr.flush()

    print(
        f"-w {chunk_w} -h {chunk_h} -z {chunk_z} --shard-width={chunk_w} --shard-height={chunk_h} --shard-depth={chunk_z}"
        #f"--shard-width={shard_w} --shard-height={shard_h} --shard-depth={shard_depth} "
        # sharding doesn't work properly yet (shard == chunk disables it practically): 
        # https://github.com/glencoesoftware/bioformats2raw/issues/299
    )
    print(
        f"Resulting ChunkSize={chunk_size // 1024}kb",
        file=sys.stderr,
    )
    sys.stderr.flush()


if __name__ == "__main__":
    main()
