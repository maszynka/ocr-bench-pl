#!/usr/bin/env python3
"""
OCR Benchmark — Polish documents, cross-platform (Windows + macOS).

Pipeline: input/<doc_folder>/<page>.jpg|pdf  ->  one merged DOCX + ODT per method.

Engines compared:
  - tess_best_psm1   Tesseract 5.x, tessdata_best, PSM 1 (auto + OSD)
  - tess_best_psm4   Tesseract 5.x, tessdata_best, PSM 4 (single column, good for tables)
  - tess_best_psm6   Tesseract 5.x, tessdata_best, PSM 6 (uniform block)
  - tess_fast_psm1   Tesseract 5.x, tessdata_fast, PSM 1 (speed reference)
  - ocrmypdf         OCRmyPDF with --deskew --clean --rotate-pages -l pol
  - rapidocr         RapidOCR PP-OCRv5 Latin (ONNX, neural baseline)

Per-method outputs land in output/<method>/<doc_name>.{docx,odt,txt}
Metrics (CER, WER, diacritic-CER, number accuracy, time, peak RAM) go to output/results.csv
Ground-truth comparison happens automatically when ground-truth-txt/<doc_name>.txt exists.
"""
from __future__ import annotations

import argparse
import csv
import gc
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# ---------- paths ----------
ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
GT_DIR = ROOT / "ground-truth-txt"
OUTPUT_DIR = ROOT / "output"
RESULTS_CSV = OUTPUT_DIR / "results.csv"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PDF_EXTS = {".pdf"}

POLISH_DIACRITICS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")

# ---------- presets ----------
# `methods` empty means "all available". `metrics=False` skips CSV row writes
# (use for production runs where you just want artifacts).
MODES: dict[str, dict] = {
    "quick":      {"methods": [],                                       "max_pages": 2,    "metrics": False,
                   "desc": "Smoke test: 2 pages per doc, all available methods. Skips CSV — partial-page metrics vs full-doc GT are misleading."},
    "production": {"methods": ["ocrmypdf"],                             "max_pages": None, "metrics": False,
                   "desc": "Single best method (OCRmyPDF) — no benchmark CSV, just DOCX/ODT artifacts."},
    "compare":    {"methods": ["ocrmypdf", "tess_best_psm1",
                                "tess_best_psm4", "rapidocr"],          "max_pages": None, "metrics": True,
                   "desc": "Sensible head-to-head: OCRmyPDF, Tess best PSM1, Tess best PSM4 (tables), RapidOCR (numbers)."},
    "full":       {"methods": [],                                       "max_pages": None, "metrics": True,
                   "desc": "All 6 methods — full benchmark with metrics."},
}


# ---------- lazy imports / runtime checks ----------
def _import_pillow():
    from PIL import Image  # noqa: F401
    return Image


def _import_pdf2image():
    from pdf2image import convert_from_path
    return convert_from_path


def _import_pytesseract():
    import pytesseract
    # Allow override via env (Windows users often install to a non-PATH location)
    env_path = os.environ.get("TESSERACT_CMD")
    if env_path:
        pytesseract.pytesseract.tesseract_cmd = env_path
    return pytesseract


def _tesseract_version() -> str:
    try:
        pyt = _import_pytesseract()
        return str(pyt.get_tesseract_version())
    except Exception as e:
        return f"unavailable ({e})"


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


# ---------- I/O helpers ----------
def list_input_docs() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    return sorted([p for p in INPUT_DIR.iterdir() if p.is_dir()])


def list_pages(doc: Path) -> list[Path]:
    pages: list[Path] = []
    for p in sorted(doc.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith("."):  # .DS_Store etc.
            continue
        if p.suffix.lower() in IMAGE_EXTS or p.suffix.lower() in PDF_EXTS:
            pages.append(p)
    return pages


def pdf_to_images(pdf_path: Path, dpi: int = 300) -> list:
    convert = _import_pdf2image()
    return convert(str(pdf_path), dpi=dpi, fmt="png")


def page_to_pil_image(page_path: Path):
    Image = _import_pillow()
    if page_path.suffix.lower() in PDF_EXTS:
        return pdf_to_images(page_path)
    return [Image.open(str(page_path))]


# ---------- OCR engines ----------
@dataclass
class OcrResult:
    text: str
    seconds: float = 0.0
    peak_rss_mb: float = 0.0
    error: str | None = None


def _measure(callable_: Callable[[], str]) -> OcrResult:
    """Run an OCR closure, capture time and peak RSS (best-effort, cross-platform)."""
    import psutil

    proc = psutil.Process(os.getpid())
    gc.collect()
    rss_before = proc.memory_info().rss
    peak = rss_before
    t0 = time.perf_counter()
    try:
        text = callable_()
    except Exception as e:
        return OcrResult(text="", seconds=time.perf_counter() - t0, error=f"{type(e).__name__}: {e}")
    seconds = time.perf_counter() - t0
    # Sample again at the end — we cannot truly track peak without a sampler thread,
    # but RSS post-call is a reasonable proxy after large allocations.
    rss_after = proc.memory_info().rss
    peak = max(peak, rss_after)
    return OcrResult(text=text, seconds=seconds, peak_rss_mb=peak / (1024 * 1024))


# ---- Tesseract ----
def _tess_ocr(pages: list[Path], psm: int, tessdata_dir: Path | None, lang: str = "pol") -> str:
    pyt = _import_pytesseract()
    config_parts = [
        f"--psm {psm}",
        "--oem 1",
        "-c preserve_interword_spaces=1",
    ]
    if tessdata_dir:
        config_parts.append(f'--tessdata-dir "{tessdata_dir}"')
    config = " ".join(config_parts)

    chunks: list[str] = []
    for page in pages:
        for img in page_to_pil_image(page):
            txt = pyt.image_to_string(img, lang=lang, config=config)
            chunks.append(txt.rstrip())
    return "\n\n".join(chunks).strip() + "\n"


# ---- OCRmyPDF ----
def _ocrmypdf_ocr(pages: list[Path], lang: str = "pol") -> str:
    import ocrmypdf
    from PIL import Image as PILImage

    with tempfile.TemporaryDirectory(prefix="ocr_bench_") as tmp:
        tmp_p = Path(tmp)
        # Build a single PDF from input pages so we hand OCRmyPDF one job.
        merged_in = tmp_p / "input.pdf"
        images = []
        for page in pages:
            for img in page_to_pil_image(page):
                if img.mode != "RGB":
                    img = img.convert("RGB")
                images.append(img)
        if not images:
            return ""
        first, rest = images[0], images[1:]
        first.save(str(merged_in), "PDF", save_all=True, append_images=rest, resolution=300.0)

        out_pdf = tmp_p / "out.pdf"
        out_txt = tmp_p / "out.txt"
        # --force-ocr because our generated PDF will not have a text layer; safer than rotating
        # detection logic. --output-type pdf keeps things simple.
        ocrmypdf.ocr(
            str(merged_in),
            str(out_pdf),
            language=[lang],
            deskew=True,
            clean=True,
            rotate_pages=True,
            force_ocr=True,
            output_type="pdf",
            sidecar=str(out_txt),
            progress_bar=False,
            jobs=max(1, (os.cpu_count() or 2) - 1),
        )
        return out_txt.read_text(encoding="utf-8", errors="replace")


# ---- RapidOCR ----
_RAPID_OCR_INSTANCE = None


def _get_rapidocr():
    """RapidOCR with PP-OCRv5 Latin recognition (Polish-compatible).

    The default RapidOCR config loads the Chinese PP-OCRv4 model, which silently
    drops Polish diacritics. We explicitly select PP-OCRv5 + LATIN recognition
    + MULTI-lang detection — the configuration documented in the field report.
    """
    global _RAPID_OCR_INSTANCE
    if _RAPID_OCR_INSTANCE is None:
        from rapidocr import RapidOCR, LangDet, LangRec, OCRVersion
        # Detection is layout-only (lang-agnostic); recognition is LATIN for Polish.
        # PP-OCRv5 detection currently ships only with LangDet.CH variant — that
        # combo is the valid one per RapidOCR 3.8 model list.
        _RAPID_OCR_INSTANCE = RapidOCR(
            params={
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.lang_type": LangDet.CH,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.lang_type": LangRec.LATIN,
            }
        )
    return _RAPID_OCR_INSTANCE


def _rapidocr_ocr(pages: list[Path]) -> str:
    import numpy as np

    rapid = _get_rapidocr()
    chunks: list[str] = []
    for page in pages:
        for img in page_to_pil_image(page):
            if img.mode != "RGB":
                img = img.convert("RGB")
            arr = np.array(img)
            out = rapid(arr)
            boxes = getattr(out, "boxes", None)
            txts = getattr(out, "txts", None)
            if not txts:
                continue
            # Sort top-to-bottom, left-to-right by box top-left when available.
            if boxes is not None and len(boxes) == len(txts):
                paired = sorted(zip(boxes, txts), key=lambda bt: (bt[0][0][1], bt[0][0][0]))
                lines = [t for _, t in paired]
            else:
                lines = list(txts)
            chunks.append("\n".join(lines))
    return "\n\n".join(chunks).strip() + "\n"


# ---------- method registry ----------
@dataclass
class Method:
    name: str
    description: str
    runner: Callable[[list[Path]], str]
    requires: list[str] = field(default_factory=list)


def _resolve_tessdata(variant: str) -> Path | None:
    """Look up a tessdata override directory; falls back to system default."""
    env_var = "TESSDATA_BEST_DIR" if variant == "best" else "TESSDATA_FAST_DIR"
    p = os.environ.get(env_var)
    return Path(p) if p else None


def build_methods() -> list[Method]:
    tess_ok = bool(_which("tesseract") or os.environ.get("TESSERACT_CMD"))
    ocrmypdf_ok = bool(_which("ocrmypdf")) or _module_available("ocrmypdf")
    rapid_ok = _module_available("rapidocr")

    methods: list[Method] = []
    if tess_ok:
        methods.extend(
            [
                Method(
                    "tess_best_psm1",
                    "Tesseract tessdata_best PSM 1 (auto + OSD)",
                    lambda pages: _tess_ocr(pages, psm=1, tessdata_dir=_resolve_tessdata("best")),
                ),
                Method(
                    "tess_best_psm4",
                    "Tesseract tessdata_best PSM 4 (single column, tables)",
                    lambda pages: _tess_ocr(pages, psm=4, tessdata_dir=_resolve_tessdata("best")),
                ),
                Method(
                    "tess_best_psm6",
                    "Tesseract tessdata_best PSM 6 (uniform block)",
                    lambda pages: _tess_ocr(pages, psm=6, tessdata_dir=_resolve_tessdata("best")),
                ),
                Method(
                    "tess_fast_psm1",
                    "Tesseract tessdata_fast PSM 1 (speed baseline)",
                    lambda pages: _tess_ocr(pages, psm=1, tessdata_dir=_resolve_tessdata("fast")),
                ),
            ]
        )
    if ocrmypdf_ok and tess_ok:
        methods.append(
            Method(
                "ocrmypdf",
                "OCRmyPDF (Tesseract + deskew/clean/rotate)",
                lambda pages: _ocrmypdf_ocr(pages),
            )
        )
    if rapid_ok:
        methods.append(
            Method(
                "rapidocr",
                "RapidOCR PP-OCRv5 Latin (ONNX neural baseline)",
                lambda pages: _rapidocr_ocr(pages),
            )
        )
    return methods


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


# ---------- exporters ----------
def export_docx(text: str, path: Path) -> None:
    from docx import Document

    doc = Document()
    for para in text.split("\n\n"):
        for line in para.splitlines() or [""]:
            doc.add_paragraph(line)
        doc.add_paragraph("")  # paragraph break
    doc.save(str(path))


def export_odt(text: str, path: Path) -> None:
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    doc = OpenDocumentText()
    for para in text.split("\n\n"):
        for line in para.splitlines() or [""]:
            doc.text.addElement(P(text=line))
        doc.text.addElement(P(text=""))
    doc.save(str(path))


# ---------- metrics ----------
def _normalize_for_metrics(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _diacritic_only(s: str) -> str:
    return "".join(ch for ch in s if ch in POLISH_DIACRITICS)


def _numbers_only(s: str) -> list[str]:
    return re.findall(r"\d[\d.,]*", s)


def compute_metrics(hypothesis: str, reference: str) -> dict[str, float]:
    """Return CER, WER, diacritic-CER, number-accuracy."""
    import jiwer

    h = _normalize_for_metrics(hypothesis)
    r = _normalize_for_metrics(reference)
    if not r:
        return {}

    out: dict[str, float] = {}
    try:
        out["cer"] = jiwer.cer(r, h)
        out["wer"] = jiwer.wer(r, h)
    except Exception:
        pass

    h_diac, r_diac = _diacritic_only(h), _diacritic_only(r)
    if r_diac:
        try:
            out["diacritic_cer"] = jiwer.cer(r_diac, h_diac) if h_diac else 1.0
        except Exception:
            pass

    r_nums = _numbers_only(r)
    h_nums = set(_numbers_only(h))
    if r_nums:
        recovered = sum(1 for n in r_nums if n in h_nums)
        out["number_accuracy"] = recovered / len(r_nums)

    out["len_hyp_chars"] = len(h)
    out["len_ref_chars"] = len(r)
    return out


# ---------- runner ----------
def slugify(name: str) -> str:
    s = re.sub(r"[^\w\-. ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", "_", name)
    s = re.sub(r"\s+", "_", s).strip("._")
    return s or "doc"


def ground_truth_for(doc_name: str) -> str | None:
    if not GT_DIR.exists():
        return None
    candidate = GT_DIR / f"{doc_name}.txt"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8", errors="replace")
    return None


def run_benchmark(
    only_methods: list[str] | None = None,
    only_docs: list[str] | None = None,
    page_limit: int | None = None,
    write_metrics: bool = True,
) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    docs = list_input_docs()
    if only_docs:
        docs = [d for d in docs if d.name in only_docs]
    if not docs:
        print(f"[!] No input documents found under {INPUT_DIR}.", file=sys.stderr)
        sys.exit(2)

    methods = build_methods()
    if only_methods:
        methods = [m for m in methods if m.name in only_methods]
    if not methods:
        print("[!] No OCR methods available. Check dependencies (tesseract / ocrmypdf / rapidocr).", file=sys.stderr)
        sys.exit(2)

    print(f"[i] Platform: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"[i] Python:   {sys.version.split()[0]}")
    print(f"[i] Tesseract: {_tesseract_version()}")
    print(f"[i] Documents: {len(docs)}    Methods: {len(methods)}    Metrics: {write_metrics}")

    # No-op CSV writer for production runs.
    csvf = RESULTS_CSV.open("a", encoding="utf-8", newline="") if write_metrics else open(os.devnull, "w")
    try:
        writer = csv.writer(csvf)
        if write_metrics and RESULTS_CSV.stat().st_size == 0:
            writer.writerow(
                [
                    "timestamp", "platform", "method", "document", "pages",
                    "seconds", "peak_rss_mb", "len_hyp_chars", "len_ref_chars",
                    "cer", "wer", "diacritic_cer", "number_accuracy", "error",
                ]
            )

        for doc in docs:
            pages = list_pages(doc)
            if page_limit:
                pages = pages[:page_limit]
            if not pages:
                print(f"[!] {doc.name}: no pages found, skipping")
                continue
            gt = ground_truth_for(doc.name)
            print(f"\n=== {doc.name} ({len(pages)} pages, gt={'yes' if gt else 'no'}) ===")

            for m in methods:
                out_method_dir = OUTPUT_DIR / m.name
                out_method_dir.mkdir(parents=True, exist_ok=True)
                slug = slugify(doc.name)
                print(f"  -> {m.name} ... ", end="", flush=True)

                res = _measure(lambda: m.runner(pages))
                if res.error:
                    print(f"ERROR ({res.error})")
                    writer.writerow(
                        [
                            time.strftime("%Y-%m-%d %H:%M:%S"), platform.system(),
                            m.name, doc.name, len(pages),
                            f"{res.seconds:.2f}", f"{res.peak_rss_mb:.1f}",
                            "", "", "", "", "", "", res.error,
                        ]
                    )
                    continue

                # Save artifacts
                (out_method_dir / f"{slug}.txt").write_text(res.text, encoding="utf-8")
                try:
                    export_docx(res.text, out_method_dir / f"{slug}.docx")
                    export_odt(res.text, out_method_dir / f"{slug}.odt")
                except Exception as e:
                    print(f"(export warn: {e}) ", end="")

                metrics = compute_metrics(res.text, gt) if gt else {}
                summary = (
                    f"{res.seconds:6.1f}s  RSS~{res.peak_rss_mb:5.0f}MB  "
                    f"chars={len(res.text):>6}"
                )
                if metrics:
                    summary += (
                        f"  CER={metrics.get('cer', float('nan')):.3f}"
                        f"  WER={metrics.get('wer', float('nan')):.3f}"
                        f"  diacCER={metrics.get('diacritic_cer', float('nan')):.3f}"
                        f"  num={metrics.get('number_accuracy', float('nan')):.3f}"
                    )
                print(summary)

                writer.writerow(
                    [
                        time.strftime("%Y-%m-%d %H:%M:%S"), platform.system(),
                        m.name, doc.name, len(pages),
                        f"{res.seconds:.2f}", f"{res.peak_rss_mb:.1f}",
                        metrics.get("len_hyp_chars", ""), metrics.get("len_ref_chars", ""),
                        f"{metrics.get('cer', ''):.4f}" if "cer" in metrics else "",
                        f"{metrics.get('wer', ''):.4f}" if "wer" in metrics else "",
                        f"{metrics.get('diacritic_cer', ''):.4f}" if "diacritic_cer" in metrics else "",
                        f"{metrics.get('number_accuracy', ''):.4f}" if "number_accuracy" in metrics else "",
                        "",
                    ]
                )
                csvf.flush()
    finally:
        csvf.close()

    if write_metrics:
        print(f"\n[i] Done. Results: {RESULTS_CSV}")
    print(f"[i] Per-method outputs: {OUTPUT_DIR}/<method>/<doc>.{{txt,docx,odt}}")


# ---------- watch mode ----------
def watch_loop(
    only_methods: list[str] | None,
    page_limit: int | None,
    write_metrics: bool,
    debounce_sec: float = 5.0,
) -> None:
    """Watch INPUT_DIR for new/modified files; debounce; trigger benchmark per touched doc.

    A 'doc' is a top-level folder under input/. When any file under that folder
    changes (scan dumps new pages), we wait debounce_sec of quiet, then OCR
    just that doc. Works with any scanner that saves to input/<doc>/.
    """
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    import threading

    INPUT_DIR.mkdir(exist_ok=True)
    pending: dict[str, threading.Timer] = {}
    lock = threading.Lock()

    def _process(doc_name: str) -> None:
        with lock:
            pending.pop(doc_name, None)
        print(f"\n[watch] processing '{doc_name}' ...", flush=True)
        try:
            run_benchmark(
                only_methods=only_methods,
                only_docs=[doc_name],
                page_limit=page_limit,
                write_metrics=write_metrics,
            )
        except Exception as e:
            print(f"[watch] error processing '{doc_name}': {e}", file=sys.stderr)
        print(f"[watch] '{doc_name}' done. Listening...\n", flush=True)

    def _schedule(doc_name: str) -> None:
        with lock:
            t = pending.get(doc_name)
            if t:
                t.cancel()
            t = threading.Timer(debounce_sec, _process, args=(doc_name,))
            t.daemon = True
            pending[doc_name] = t
            t.start()

    class Handler(FileSystemEventHandler):
        def _doc_for(self, path: str) -> str | None:
            try:
                rel = Path(path).resolve().relative_to(INPUT_DIR.resolve())
            except ValueError:
                return None
            parts = rel.parts
            if not parts:
                return None
            return parts[0]

        def _maybe(self, event):
            if event.is_directory:
                return
            ext = Path(event.src_path).suffix.lower()
            if ext not in IMAGE_EXTS and ext not in PDF_EXTS:
                return
            if Path(event.src_path).name.startswith("."):
                return
            doc = self._doc_for(event.src_path)
            if doc:
                _schedule(doc)

        on_created = _maybe
        on_modified = _maybe
        on_moved = lambda self, e: self._maybe(type("E", (), {"is_directory": e.is_directory, "src_path": e.dest_path})())

    observer = Observer()
    observer.schedule(Handler(), str(INPUT_DIR), recursive=True)
    observer.start()
    print(f"[watch] watching {INPUT_DIR} (debounce {debounce_sec}s). Ctrl+C to stop.")
    print(f"[watch] drop scanned files under input/<doc_folder>/ — OCR runs automatically.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[watch] stopping...")
    finally:
        observer.stop()
        observer.join()


# ---------- doctor / preflight ----------
def doctor() -> int:
    print(f"Platform        : {platform.system()} {platform.release()} {platform.machine()}")
    print(f"Python          : {sys.version.split()[0]} ({sys.executable})")
    print(f"tesseract       : {_tesseract_version()}")
    print(f"pdftotext (path): {_which('pdftotext') or 'NOT FOUND'}")
    print(f"pdftoppm  (path): {_which('pdftoppm')  or 'NOT FOUND'}")
    print(f"gs / gswin64c   : {_which('gs') or _which('gswin64c') or 'NOT FOUND'}")
    for mod in ("pytesseract", "ocrmypdf", "rapidocr", "PIL", "pdf2image",
                "docx", "odf", "jiwer", "psutil"):
        ok = _module_available(mod)
        print(f"py:{mod:<22} {'OK' if ok else 'MISSING'}")
    methods = build_methods()
    print(f"\nAvailable methods: {[m.name for m in methods] or 'NONE — install deps'}")
    print(f"Input docs:        {[d.name for d in list_input_docs()]}")
    gts = sorted([p.stem for p in GT_DIR.glob('*.txt')]) if GT_DIR.exists() else []
    print(f"Ground-truth txt:  {gts}")
    return 0 if methods and list_input_docs() else 1


def main() -> int:
    modes_help = "\n".join(f"  {k:<11} {v['desc']}" for k, v in MODES.items())
    ap = argparse.ArgumentParser(
        description="OCR benchmark for Polish documents (cross-platform).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Modes (--mode):\n{modes_help}",
    )
    ap.add_argument("--doctor", action="store_true", help="Check dependencies and exit.")
    ap.add_argument("--mode", choices=list(MODES.keys()), default="full",
                    help="Preset bundle of methods + flags. Overridden by explicit --method / --max-pages.")
    ap.add_argument("--method", action="append", default=[], help="Run only this method (repeatable). Overrides --mode methods.")
    ap.add_argument("--doc", action="append", default=[], help="Run only this input doc folder (repeatable).")
    ap.add_argument("--max-pages", type=int, default=None, help="Limit pages per doc (overrides --mode).")
    ap.add_argument("--no-metrics", action="store_true", help="Skip CSV metrics row (artifacts still produced).")
    ap.add_argument("--watch", action="store_true",
                    help="Watch input/ for new files and OCR each touched doc folder automatically.")
    ap.add_argument("--watch-debounce", type=float, default=5.0,
                    help="Seconds of quiet after the last file change before triggering OCR (default 5).")
    args = ap.parse_args()

    if args.doctor:
        return doctor()

    preset = MODES[args.mode]
    methods = args.method or preset["methods"] or None
    page_limit = args.max_pages if args.max_pages is not None else preset["max_pages"]
    write_metrics = preset["metrics"] and not args.no_metrics

    try:
        if args.watch:
            watch_loop(only_methods=methods, page_limit=page_limit,
                       write_metrics=write_metrics, debounce_sec=args.watch_debounce)
        else:
            run_benchmark(
                only_methods=methods,
                only_docs=args.doc or None,
                page_limit=page_limit,
                write_metrics=write_metrics,
            )
    except KeyboardInterrupt:
        print("\n[!] Interrupted.")
        return 130
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
