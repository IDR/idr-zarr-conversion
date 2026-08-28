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
    size_t: int,
    size_c: int,
    pixel_type: str,
) -> tuple[int, int, int, int, int, int, int, int]:
    """Return chunk and shard dimensions for bioformats2raw.

    Parameters are returned as ``(chunk_w, chunk_h, chunk_z,
    shard_w, shard_h, shard_depth, chunk_size, shard_size)``.

    Chunk dimensions are grown from 32x32x1, roughly balancing X and Y, until
    the in-memory chunk size reaches ``TARGET_CHUNK_SIZE``. Once X and Y hit
    the image bounds, Z is increased. If the whole image is smaller than the
    target chunk, the chunk is capped at the image size.

    Shard dimensions start from the chosen chunk dimensions and are grown in
    the same way until ``TARGET_SHARD_SIZE`` is reached, capped at the image
    size in each axis.

    ``chunk_size`` and ``shard_size`` are the full in-memory byte sizes of a
    single chunk/shard, accounting for the pixel type.
    """
    bpp = bytes_per_pixel(pixel_type)
    max_z = max(size_z, 1)

    chunk_w = min(32, width)
    chunk_h = min(32, height)
    chunk_z = 1

    while chunk_w * chunk_h * chunk_z * bpp < TARGET_CHUNK_SIZE:
        # Grow the smaller in-plane dimension first to keep chunks roughly
        # square; if one dimension is already at the image bound, grow the
        # other. Only move to Z once both X and Y are capped.
        if chunk_w < width and (chunk_w <= chunk_h or chunk_h >= height):
            chunk_w = min(width, chunk_w * 2)
        elif chunk_h < height:
            chunk_h = min(height, chunk_h * 2)
        elif chunk_z < max_z:
            chunk_z = min(max_z, chunk_z + 1)
        else:
            break

    # Shards must be whole multiples of the chosen chunk dimensions in each
    # axis. Start with a 1x1x1 chunk grid per shard and greedily increase the
    # per-axis chunk count until the target shard size is reached or no axis
    # can be expanded further.
    max_fw = width // chunk_w
    max_fh = height // chunk_h
    max_fz = max_z // chunk_z

    fw = fh = fz = 1
    while True:
        current_size = chunk_w * fw * chunk_h * fh * chunk_z * fz * bpp
        best = None
        best_size = current_size

        for dim, new_factor, dim_max in (
            ("w", fw + 1, max_fw),
            ("h", fh + 1, max_fh),
            ("z", fz + 1, max_fz),
        ):
            if new_factor > dim_max:
                continue
            if dim == "w":
                size = chunk_w * new_factor * chunk_h * fh * chunk_z * fz * bpp
            elif dim == "h":
                size = chunk_w * fw * chunk_h * new_factor * chunk_z * fz * bpp
            else:
                size = chunk_w * fw * chunk_h * fh * chunk_z * new_factor * bpp
            if size <= TARGET_SHARD_SIZE and size > best_size:
                best = dim
                best_size = size

        if best is None:
            break

        if best == "w":
            fw += 1
        elif best == "h":
            fh += 1
        else:
            fz += 1

    shard_w = chunk_w * fw
    shard_h = chunk_h * fh
    shard_depth = chunk_z * fz

    chunk_size = chunk_w * chunk_h * chunk_z * bpp
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
    ) = derive_parameters(width, height, size_z, size_t, size_c, pixel_type)

    print(
        f"Image: Width={width} Height={height} SizeZ={size_z} "
        f"SizeT={size_t} SizeC={size_c} PixelType={pixel_type}\n"
        f"bioformats2raw parameters:",
        file=sys.stderr,
    )
    sys.stderr.flush()

    print(
        f"--ngff-version=0.5 --downsample-type=AREA -c zstd --compression-properties='level=1' "
        f"-w {chunk_w} -h {chunk_h} -z {chunk_z} "
        f"--shard-width={shard_w} --shard-height={shard_h} --shard-depth={shard_depth}"
    )
    print(
        f"Resulting ChunkSize={chunk_size // 1024}kb and ShardSize={shard_size // 1024}kb",
        file=sys.stderr,
    )
    sys.stderr.flush()


if __name__ == "__main__":
    main()
