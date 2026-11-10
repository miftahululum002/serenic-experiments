#!/usr/bin/env python3
"""Convert an image into favicon-ready PNG, ICO, and SVG files at multiple sizes.

Usage:
    python favicon_converter.py --file logo.png
    python favicon_converter.py --file logo.png --sizes 16,32,48,64,128,256 --formats png,ico,svg
    python favicon_converter.py --file logo.png --output-dir ./favicons
    python favicon_converter.py --file logo.png --width 800            # tinggi dihitung otomatis
    python favicon_converter.py --file logo.png --width 800 --height 600  # fit di dalam box, rasio terjaga

Default output: output/<YYYYMMDD>/<HHMMSS>/ (timestamped per run).
"""

import argparse
import base64
import io
import os
import sys
from datetime import datetime

from PIL import Image, UnidentifiedImageError

DEFAULT_SIZES = [16, 32, 48, 64, 128, 256]
DEFAULT_FORMATS = ["png", "ico", "svg"]
BASE_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def default_output_dir() -> str:
    """output/{YYYYMMDD}/{HHMMSS}, timestamped at the moment the script runs."""
    now = datetime.now()
    return os.path.join(BASE_OUTPUT_DIR, now.strftime("%Y%m%d"), now.strftime("%H%M%S"))


def validate_image(file_path: str) -> Image.Image:
    """Make sure --file points to a real, readable image. Raises on failure."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File tidak ditemukan: {file_path}")

    try:
        img = Image.open(file_path)
        img.verify()  # cek integritas file tanpa decode penuh
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"'{file_path}' bukan file gambar yang valid: {exc}") from exc

    # verify() menutup file internal, buka ulang untuk dipakai
    return Image.open(file_path)


def parse_sizes(sizes_arg: str) -> list[int]:
    try:
        sizes = [int(s.strip()) for s in sizes_arg.split(",") if s.strip()]
    except ValueError as exc:
        raise ValueError(f"--sizes harus daftar angka dipisah koma, contoh: 16,32,64 ({exc})") from exc

    if not sizes or any(s <= 0 for s in sizes):
        raise ValueError("--sizes harus berisi angka positif")
    return sorted(set(sizes))


def resize_image(img: Image.Image, size: int) -> Image.Image:
    """Resize via resampling only — no crop, no recolor, no background fill."""
    return img.resize((size, size), Image.LANCZOS)


def resize_keep_ratio(img: Image.Image, width: int | None, height: int | None) -> Image.Image:
    """Resize to a custom dimension while preserving the original aspect ratio.

    - Only --width given: height is derived from the ratio.
    - Only --height given: width is derived from the ratio.
    - Both given: image is scaled to fit inside the width x height box
      (never exceeds it), so the output may be smaller than the box on
      one axis instead of distorting or cropping the image.
    """
    orig_w, orig_h = img.size

    if width and height:
        ratio = min(width / orig_w, height / orig_h)
    elif width:
        ratio = width / orig_w
    elif height:
        ratio = height / orig_h
    else:
        raise ValueError("resize_keep_ratio butuh --width dan/atau --height")

    new_w = max(1, round(orig_w * ratio))
    new_h = max(1, round(orig_h * ratio))
    return img.resize((new_w, new_h), Image.LANCZOS)


def save_png(resized: dict[int, Image.Image], output_dir: str, basename: str) -> list[str]:
    paths = []
    for size, img in resized.items():
        path = os.path.join(output_dir, f"{basename}-{size}x{size}.png")
        img.save(path, format="PNG")
        paths.append(path)
    return paths


def save_ico(resized: dict[int, Image.Image], output_dir: str, basename: str) -> str:
    sizes_sorted = sorted(resized)
    base_img = resized[sizes_sorted[0]]
    other_imgs = [resized[s] for s in sizes_sorted[1:]]

    path = os.path.join(output_dir, f"{basename}.ico")
    base_img.save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes_sorted],
        append_images=other_imgs,
    )
    return path


def save_svg(img: Image.Image, output_dir: str, basename: str) -> str:
    """Embed the original raster image (unmodified) inside an SVG wrapper.

    A true raster-to-vector trace would alter the artwork's shapes, which
    contradicts "tanpa mengubah gambar". Embedding keeps pixels identical
    while still producing a valid favicon.svg.
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    width, height = img.size

    svg_content = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<image width="{width}" height="{height}" '
        f'href="data:image/png;base64,{b64}"/></svg>'
    )

    path = os.path.join(output_dir, f"{basename}.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Konversi gambar menjadi PNG/ICO/SVG favicon dengan berbagai ukuran, "
        "tanpa mengubah konten atau background gambar."
    )
    parser.add_argument("--file", required=True, help="Path ke file gambar sumber (wajib berupa gambar)")
    parser.add_argument(
        "--sizes",
        default=",".join(str(s) for s in DEFAULT_SIZES),
        help=f"Daftar ukuran (px) dipisah koma, default: {','.join(str(s) for s in DEFAULT_SIZES)}",
    )
    parser.add_argument(
        "--formats",
        default=",".join(DEFAULT_FORMATS),
        help=f"Format output dipisah koma dari {DEFAULT_FORMATS}, default: semua",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Folder output (dibuat otomatis jika belum ada), default: output/<YYYYMMDD>/<HHMMSS>",
    )
    parser.add_argument(
        "--basename",
        default="favicon",
        help="Prefix nama file output, default: favicon",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Lebar custom (px) untuk resize tambahan dengan rasio gambar tetap terjaga",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Tinggi custom (px) untuk resize tambahan dengan rasio gambar tetap terjaga",
    )

    args = parser.parse_args()

    try:
        img = validate_image(args.file)
        sizes = parse_sizes(args.sizes)
        formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
        invalid_formats = set(formats) - set(DEFAULT_FORMATS)
        if invalid_formats:
            raise ValueError(f"Format tidak dikenal: {sorted(invalid_formats)}. Pilihan: {DEFAULT_FORMATS}")
        if args.width is not None and args.width <= 0:
            raise ValueError("--width harus angka positif")
        if args.height is not None and args.height <= 0:
            raise ValueError("--height harus angka positif")
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output_dir = args.output_dir or default_output_dir()
    os.makedirs(output_dir, exist_ok=True)

    # Resize sekali per ukuran, dipakai ulang untuk png/ico supaya konsisten
    resized = {size: resize_image(img, size) for size in sizes}

    written = []
    if "png" in formats:
        written += save_png(resized, output_dir, args.basename)
    if "ico" in formats:
        written.append(save_ico(resized, output_dir, args.basename))
    if "svg" in formats:
        # SVG pakai ukuran terbesar supaya tetap tajam saat di-scale browser
        largest = resized[max(resized)]
        written.append(save_svg(largest, output_dir, args.basename))

    if args.width or args.height:
        custom_img = resize_keep_ratio(img, args.width, args.height)
        cw, ch = custom_img.size
        custom_path = os.path.join(output_dir, f"{args.basename}-{cw}x{ch}.png")
        custom_img.save(custom_path, format="PNG")
        written.append(custom_path)

    print("Berhasil membuat:")
    for path in written:
        print(f"  - {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
