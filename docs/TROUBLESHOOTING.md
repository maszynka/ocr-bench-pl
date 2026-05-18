# Troubleshooting — co jak coś pójdzie nie tak

Run `python benchmark.py --doctor` first whenever something breaks. Most failures here surface as a `MISSING` line in its output.

Symptoms are grouped by **where** they fail: setup, single OCR run, watch mode, output quality. Each entry: *symptom → cause → fix*.

---

## 1. Setup / dependency failures

### `TesseractNotFoundError: tesseract is not installed or it's not in your PATH`
- **Cause**: `tesseract` binary missing or not on `PATH`. Windows: most likely. macOS Apple Silicon: `brew --prefix` puts it under `/opt/homebrew/bin` which is sometimes not on PATH for non-interactive shells.
- **Fix**:
  - Linux: `sudo apt install tesseract-ocr tesseract-ocr-pol tesseract-ocr-osd`
  - macOS: `brew install tesseract tesseract-lang`; if still missing, `export PATH="/opt/homebrew/bin:$PATH"` in `~/.zshrc`
  - Windows: install from [UB Mannheim builds](https://github.com/UB-Mannheim/tesseract/wiki); then either add `C:\Program Files\Tesseract-OCR\` to PATH **or** set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in the shell that runs the benchmark.

### `Error opening data file pol.traineddata` (or `eng`, `osd`)
- **Cause**: `TESSDATA_BEST_DIR` / `TESSDATA_FAST_DIR` points to a directory that doesn't contain the language file. Tesseract does **not** auto-download.
- **Fix**: verify the directory has `pol.traineddata` *and* `osd.traineddata` (PSM 1 needs OSD), then re-export the variable. Download:
  ```bash
  mkdir -p ~/tessdata_best
  for f in pol.traineddata osd.traineddata eng.traineddata; do
    curl -fsSL "https://github.com/tesseract-ocr/tessdata_best/raw/main/$f" -o "$HOME/tessdata_best/$f"
  done
  export TESSDATA_BEST_DIR="$HOME/tessdata_best"
  ```

### `TESSDATA_BEST_DIR` set but accuracy looks like `tessdata_fast`
- **Cause**: variable is set to a *nonexistent* directory. Tesseract silently falls back to the system tessdata (which is the medium-quality LSTM, not `_best`).
- **Fix**: `ls "$TESSDATA_BEST_DIR/pol.traineddata"` — if empty, fix the path or re-download (above).

### `pdf2image.exceptions.PDFInfoNotInstalledError: Unable to get page count. Is poppler installed and in PATH?`
- **Cause**: `pdftoppm` / `pdftotext` (Poppler) missing. Only triggers when the input contains PDFs.
- **Fix**: Linux: `sudo apt install poppler-utils`. macOS: `brew install poppler`. Windows: download [Poppler for Windows (oschwartz10612)](https://github.com/oschwartz10612/poppler-windows/releases), unzip, add `Library\bin` to PATH. The doctor catches this.

### `ocrmypdf` errors with `Could not find ghostscript executable`
- **Cause**: Ghostscript missing. OCRmyPDF needs it to rasterize PDFs internally.
- **Fix**: Linux: `sudo apt install ghostscript`. macOS: `brew install ghostscript`. Windows: install from [Artifex Ghostscript downloads](https://www.ghostscript.com/releases/) (the setup script does this via `winget`).

### `unpaper` missing warning from OCRmyPDF (`--clean` step skipped)
- **Cause**: `unpaper` binary missing. OCRmyPDF still works but skips the page-cleaning step.
- **Fix**: install it (`apt install unpaper` / `brew install unpaper`). Not critical — affects CER by maybe 0.1–0.3 pp on already-clean scans, more on dirty ones.

### RapidOCR first run downloads models, then we want to run offline
- **Cause**: RapidOCR fetches `~12 MB` of ONNX models on first invocation from `modelscope.cn`. An air-gapped machine fails on first use.
- **Fix**: run `python benchmark.py --method rapidocr --max-pages 1 --no-metrics` once on a connected machine, then copy the cache to the offline one:
  - cache lives at `<venv>/lib/python*/site-packages/rapidocr/models/` (newer rapidocr ≥3.8) — three files: `ch_PP-OCRv5_det_mobile.onnx`, `ch_ppocr_mobile_v2.0_cls_mobile.onnx`, `latin_PP-OCRv5_rec_mobile.onnx`. Sync the directory verbatim.

### `numpy is not available` / `RapidOCR` import error on Windows
- **Cause**: stale wheel cache or wrong Python arch (e.g., 32-bit Python on 64-bit Windows).
- **Fix**: `python -m pip install --force-reinstall --no-cache-dir numpy onnxruntime rapidocr`. Use 64-bit Python 3.11+.

### macOS Gatekeeper blocks downloaded tessdata files
- **Cause**: macOS quarantines files downloaded via `curl` from a non-Apple origin in some configurations.
- **Fix**: `xattr -dr com.apple.quarantine ~/tessdata_best ~/tessdata_fast`.

---

## 2. Single OCR run failures

### `[!] No input documents found under input/`
- **Cause**: `input/` empty, or you forgot `--doc "<folder name>"` with a name that doesn't exist.
- **Fix**: `ls input/` to see real folder names. Folder names are matched **case- and accent-sensitive** — `--doc "covid"` will NOT match `COVID-19 vs Szczepienia - JPG`.

### `[!] <doc>: no pages found, skipping`
- **Cause**: the folder has no files with a recognized extension (`.jpg .jpeg .png .tif .tiff .bmp .pdf`). The common offender is **iPhone HEIC**: iOS "Scan Documents" saves `.heic` by default, which is *not* in `IMAGE_EXTS`.
- **Fix**: either convert to JPG (`heif-convert *.heic`, `magick *.heic %d.jpg`), or change iPhone setting **Settings → Camera → Formats → Most Compatible** to switch to JPG.

### Pages come out in the wrong order (`page-10` before `page-2`)
- **Cause**: file listing is alphabetical (`sorted(doc.rglob("*"))`), and non-zero-padded numbers sort lexicographically.
- **Fix**: rename to zero-padded — `page-001.jpg`, `page-002.jpg`. Most scanners offer this in their save profile (NAPS2: use `$(nnn)`; VueScan: `+` with width). One-liner to fix existing files (zsh/bash):
  ```bash
  i=1; for f in page-*.jpg; do mv "$f" "$(printf 'page-%03d.jpg' $i)"; i=$((i+1)); done
  ```

### Output filename collisions (e.g. `A B/` and `A_B/` both → `A_B.txt`)
- **Cause**: `slugify()` collapses spaces and most punctuation to `_`. Two distinct input folders can map to the same stem.
- **Fix**: rename one of the input folders to disambiguate before running.

### `ocrmypdf` errors with `InputFileError: the input file already has text content`
- **Cause**: shouldn't happen — we pass `--force-ocr` — but if you call OCRmyPDF directly outside this repo without that flag, born-digital PDFs trigger it.
- **Fix**: when invoking OCRmyPDF manually, add `--force-ocr` (or `--skip-text` if you want to keep the existing layer).

### `MemoryError` or process killed during ocrmypdf on a big PDF
- **Cause**: `jobs=cpu_count-1` parallelism × 300-DPI rasterized pages = lots of RAM. A 100-page A4 scan can peak past 3–4 GB.
- **Fix**: cap parallelism by running `OMP_NUM_THREADS=1 python benchmark.py …` and editing `_ocrmypdf_ocr` to use `jobs=1`, **or** split the input into batches of 20–30 pages.

### `PermissionError: [Errno 13]` writing to `output/`
- **Cause**: output dir not writable; on Windows often because `results.csv` is open in Excel.
- **Fix**: close Excel, or `--no-metrics` to skip CSV writes.

### Process killed mid-run, half-written DOCX/ODT/CSV left behind
- **Cause**: artifacts are written non-atomically. The CSV row is flushed per row (good), but a half-written DOCX is corrupt.
- **Fix**: delete the partial artifact and re-run that method only: `python benchmark.py --method <name> --doc "<doc>"`.

---

## 3. Watch-mode failures

### Watch daemon misses files
- **Cause**: usually one of:
  1. The scanner writes the JPG to a temp name and renames it (most scanners do). Watchdog's rename handler in this repo is brittle — moves across mount points may not fire.
  2. The file lands on a network share or cloud-sync folder before sync completes. Events fire for the placeholder, not the synced file.
  3. Debounce shorter than the scanner's per-page delay; OCR fires on the first page, ignoring later ones.
- **Fix**:
  1. Use `--watch-debounce 10` (or higher — try 30 for cloud sync).
  2. For network/cloud, prefer a **scanner post-save command** (NAPS2, VueScan) which fires only after the file is fully written.
  3. As a sanity check, `touch input/<doc>/test.jpg` while the daemon runs — you should see `[watch] processing 'doc'`.

### Watch daemon OCRs the same doc repeatedly
- **Cause**: the scanner re-writes files (touch on save), or an editor's auto-backup churns inside `input/`.
- **Fix**: increase debounce so closely-spaced events collapse; or move the daemon's target to a dedicated `incoming/` folder that nothing else touches.

### Watch daemon OCRs *before* all pages are saved
- **Cause**: ADF scanner sends pages one by one; debounce shorter than the gap between pages.
- **Fix**: set `--watch-debounce 15` (or longer for slow ADFs). The benchmark waits for `N` seconds of *quiet* before firing — increasing this only delays the OCR, doesn't degrade quality.

---

## 4. Output looks wrong (no error, just bad text)

### CER 0.8–0.95 in `--mode quick` even on clean docs
- **Cause**: by design — `quick` OCR's 2 pages and metrics compare against full-doc ground truth, so most of the GT is "missing" from the hypothesis. Pre-fix to mode (commit `eafa30b`): `quick` no longer writes CSV rows.
- **Fix**: run `--mode compare` or `--mode full` for real metrics. `quick` is a smoke test for the pipeline only.

### RapidOCR `diacritic_cer` is 40%+ on Polish docs
- **Cause**: known — see [README → Results → Takeaways](../README.md#takeaways). RapidOCR with the PP-OCRv5 Latin recognizer loses Polish-specific diacritics consistently. Not a bug in our config (we already explicitly select Latin + v5).
- **Fix**: don't use RapidOCR for Polish prose. Use it for numeric forms / receipts where diacritics don't matter.

### Tesseract output has no diacritics (all `a` where `ą` should be)
- **Cause**: ran with `--lang eng` (or default `eng` because `pol` wasn't loaded). Tesseract picks the closest match in the loaded language model.
- **Fix**: check `tesseract --list-langs` includes `pol`. If using `_resolve_tessdata`, verify `pol.traineddata` is in the target dir.

### tessdata_fast diacritic accuracy is 2–3× worse than tessdata_best
- **Cause**: expected — `_fast` uses smaller LSTM weights. Our benchmark measures this (~1.0 % vs 0.3 % diacritic_cer).
- **Fix**: use `_best` for any document where diacritics matter. Use `_fast` only when wall-clock time dominates.

### PSM 6 wrecks multi-column or layout-heavy docs
- **Cause**: PSM 6 = "uniform block of text" — Tesseract skips layout analysis, reads strictly top-down. On a page with columns or tables it interleaves columns.
- **Fix**: use PSM 1 (auto + OSD) by default, PSM 4 (single column / tables) if a doc is one tall column. PSM 6 is a niche tool.

### OCRmyPDF rotates pages unexpectedly
- **Cause**: we pass `rotate_pages=True`. OCRmyPDF detects per-page orientation via Tesseract OSD and rotates to upright. Occasionally guesses wrong on low-text pages (mostly figures).
- **Fix**: for figure-heavy docs, edit `_ocrmypdf_ocr` to use `rotate_pages=False`. The CER on text pages is unaffected.

### OCRmyPDF "lots of diacritics - possibly poor OCR" warning
- **Cause**: heuristic warning when an unusually high fraction of glyphs are non-ASCII. False positive on Polish — we *expect* lots of diacritics.
- **Fix**: ignore the warning for Polish input. It's not an error and doesn't change the output.

### Output text contains gibberish on handwritten scans
- **Cause**: all OCR engines in this benchmark are trained on printed text. Handwriting → garbage. No error fires; the pipeline doesn't know the input was handwritten.
- **Fix**: nothing to fix in this repo. For handwriting, look at a transformer-based model (TrOCR fine-tuned on Polish) — out of scope here.

### `pismo-odreczne-pl/` produces bad text in benchmark output
- **Cause**: same as above — that input is intentional handwritten samples, used to demonstrate **what doesn't work**, not as a real target.
- **Fix**: ignore that doc's outputs. The "no ground truth" status in the README is the signal.

---

## 5. CSV / metrics oddities

### `results.csv` has rows with empty metric columns
- **Cause**: doc has no `ground-truth-txt/<doc>.txt`. Only performance metrics (`seconds`, `peak_rss_mb`) get written; metric columns stay blank.
- **Fix**: by design. Drop a GT text file next to the existing ones if you want metrics.

### Two header rows in `results.csv`
- **Cause**: shouldn't happen — header is written only when file is empty at open time. But if you `touch results.csv` between runs, the size check fails.
- **Fix**: delete the file and re-run; or open in any editor and remove the duplicate header.

### `peak_rss_mb` looks too high / monotonically increasing across methods
- **Cause**: we sample RSS post-call (no sampler thread), so the value reflects the **process** memory after the run, including caches from previous methods. Order-dependent.
- **Fix**: treat `peak_rss_mb` as a rough upper bound, not an isolated measurement. To compare methods cleanly, run each in its own process (`--method` one at a time).

---

## 6. Quick decision tree

```
something broke
├── doctor shows MISSING ────────→ install the missing dep (section 1)
├── doctor OK but command errors ─→ section 2
├── watch mode misbehaves ────────→ section 3
└── no error, just bad text ──────→ section 4 (check method, language, PSM, scan quality)
```

If none of the above match, file an issue with:
1. `python benchmark.py --doctor` output
2. The exact command you ran
3. The full traceback
4. One representative input page
