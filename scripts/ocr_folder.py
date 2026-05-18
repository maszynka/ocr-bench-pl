#!/usr/bin/env python3
"""
ocr_folder.py — OCR every page in a folder into a searchable PDF + TXT + DOCX + ODT.

Standalone wrapper around OCRmyPDF (the same engine that wins this repo's benchmark on
CER/WER and speed). Unlike `benchmark.py`, this does not require the `input/<doc>/`
layout — point it at any folder full of scans and it produces the artifacts in one go.

Examples:
  # One doc, default output dir (./ocr-out/)
  python scripts/ocr_folder.py /path/to/scans/

  # Custom output dir + output stem
  python scripts/ocr_folder.py ~/Scans/invoice-2026/ -o ~/OCR/ --name invoice-2026

  # Bulk: every immediate subfolder is its own doc
  for d in ~/Scans/*/; do python scripts/ocr_folder.py "$d" -o ~/OCR/; done

  # Switch language(s) — Polish + English mixed
  python scripts/ocr_folder.py /path/to/scans/ --lang pol+eng

Outputs (per invocation):
  <output_dir>/<name>.pdf   ← searchable PDF (OCR text layer on top of original images)
  <output_dir>/<name>.txt   ← plain text sidecar (UTF-8)
  <output_dir>/<name>.docx  ← Word document
  <output_dir>/<name>.odt   ← LibreOffice / OpenDocument

Env vars (inherited from benchmark.py setup):
  TESSERACT_CMD       full path to tesseract.exe if not on PATH (Windows)
  TESSDATA_BEST_DIR   overrides system tessdata with the LSTM-best traineddata for accuracy
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Reuse the benchmark's exporters + page discovery so output stays identical to `--mode production`.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from benchmark import (  # noqa: E402
    IMAGE_EXTS,
    PDF_EXTS,
    export_docx,
    export_odt,
    list_pages,
    slugify,
)


def _ocr_to_pdf_and_text(pages: list[Path], lang: str, out_pdf: Path, out_txt: Path) -> None:
    """Single OCRmyPDF pass over a merged PDF built from input pages.

    Kept inline (not imported from benchmark.py) because we want the searchable PDF on disk,
    not a throwaway in a tempdir like the benchmark does.
    """
    import ocrmypdf

    from benchmark import page_to_pil_image  # local import to avoid heavy top-level cost

    with tempfile.TemporaryDirectory(prefix="ocr_folder_") as tmp:
        tmp_p = Path(tmp)
        merged_in = tmp_p / "input.pdf"
        images = []
        for page in pages:
            for img in page_to_pil_image(page):
                if img.mode != "RGB":
                    img = img.convert("RGB")
                images.append(img)
        if not images:
            raise SystemExit("[!] No usable pages after image decode.")
        first, rest = images[0], images[1:]
        first.save(str(merged_in), "PDF", save_all=True, append_images=rest, resolution=300.0)

        ocrmypdf.ocr(
            str(merged_in),
            str(out_pdf),
            language=lang.split("+"),
            deskew=True,
            clean=True,
            rotate_pages=True,
            force_ocr=True,
            output_type="pdf",
            sidecar=str(out_txt),
            progress_bar=True,
            jobs=max(1, (os.cpu_count() or 2) - 1),
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="OCR a folder of scans into searchable PDF + TXT + DOCX + ODT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:", 1)[1] if "Examples:" in __doc__ else "",
    )
    ap.add_argument("input_dir", type=Path, help="Folder with JPG/PNG/PDF pages (sorted alphabetically — treat as ONE document).")
    ap.add_argument("-o", "--output-dir", type=Path, default=Path("./ocr-out"),
                    help="Where to put <name>.{pdf,txt,docx,odt} (default: ./ocr-out/).")
    ap.add_argument("--name", default=None,
                    help="Output file stem. Default: slugified input folder name.")
    ap.add_argument("--lang", default="pol",
                    help="Tesseract language(s), e.g. 'pol' or 'pol+eng' (default: pol).")
    args = ap.parse_args()

    if not args.input_dir.is_dir():
        print(f"[!] Not a directory: {args.input_dir}", file=sys.stderr)
        return 2

    pages = list_pages(args.input_dir)
    if not pages:
        accepted = sorted(IMAGE_EXTS | PDF_EXTS)
        print(f"[!] No pages found in {args.input_dir} (looking for: {' '.join(accepted)})", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or slugify(args.input_dir.resolve().name)

    out_pdf = args.output_dir / f"{stem}.pdf"
    out_txt = args.output_dir / f"{stem}.txt"
    out_docx = args.output_dir / f"{stem}.docx"
    out_odt = args.output_dir / f"{stem}.odt"

    print(f"[i] Input:  {args.input_dir}  ({len(pages)} pages, lang={args.lang})")
    print(f"[i] Output: {args.output_dir}/{stem}.{{pdf,txt,docx,odt}}")

    _ocr_to_pdf_and_text(pages, args.lang, out_pdf, out_txt)

    text = out_txt.read_text(encoding="utf-8", errors="replace")
    export_docx(text, out_docx)
    export_odt(text, out_odt)

    print(f"[ok] {out_pdf.name}  {out_txt.name}  {out_docx.name}  {out_odt.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
