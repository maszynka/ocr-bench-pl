# OCR Benchmark — Polish documents (Windows + macOS, CPU-only)

Pipeline: `input/<doc_folder>/<page>.jpg|pdf` → **one merged DOCX + ODT per OCR method**.
Designed for fully offline, RODO-friendly use. Cross-platform (one script, two installers).

Engines compared:

| ID                | Engine                                        | Notes                                       |
| ----------------- | --------------------------------------------- | ------------------------------------------- |
| `tess_best_psm1`  | Tesseract 5.x · `tessdata_best` · PSM 1       | Baseline — auto layout + OSD                |
| `tess_best_psm4`  | Tesseract 5.x · `tessdata_best` · PSM 4       | Single column / tables                      |
| `tess_best_psm6`  | Tesseract 5.x · `tessdata_best` · PSM 6       | Uniform block                               |
| `tess_fast_psm1`  | Tesseract 5.x · `tessdata_fast` · PSM 1       | Speed reference                             |
| `ocrmypdf`        | OCRmyPDF (Tesseract + deskew/clean/rotate)   | Best Tesseract result on real-world scans   |
| `rapidocr`        | RapidOCR PP-OCRv5 Latin (ONNX)               | Modern neural CPU baseline                  |

Per-method outputs land in `output/<method>/<doc>.{txt,docx,odt}`.
Aggregated metrics → `output/results.csv`.

## Setup

### macOS

```bash
bash scripts/setup_macos.sh
source .venv/bin/activate
export TESSDATA_BEST_DIR="$HOME/tessdata_best"
export TESSDATA_FAST_DIR="$HOME/tessdata_fast"
python benchmark.py --doctor
```

### Windows 10/11 (PowerShell)

```powershell
.\scripts\setup_windows.ps1
# open a NEW PowerShell window so env vars apply, then:
.\.venv\Scripts\Activate.ps1
python benchmark.py --doctor
```

`--doctor` lists detected binaries, Python modules, available methods, input docs,
and ground-truth files. Fix any `MISSING` line before running the full benchmark.

## Running

### Modes (presets)

```bash
python benchmark.py --mode quick        # 2 pages × all methods (~30s) — smoke test
python benchmark.py --mode production   # only OCRmyPDF, no CSV → DOCX/ODT artifacts
python benchmark.py --mode compare      # 4 sensible methods (OCRmyPDF, tess best PSM1/PSM4, RapidOCR)
python benchmark.py --mode full         # all 6 methods (default)
```

`--mode compare` is the recommended day-to-day benchmark — drops `tess_fast` and `tess_best_psm6`
which add noise without changing the verdict on Polish text.

### Targeted runs

```bash
python benchmark.py --method ocrmypdf            # one method only (repeatable)
python benchmark.py --doc "pismo-odreczne-pl"    # one input folder only (repeatable)
python benchmark.py --max-pages 1                # cap pages per doc (overrides --mode)
python benchmark.py --no-metrics                 # produce artifacts but skip CSV row
```

Explicit `--method` / `--max-pages` override the chosen `--mode` defaults.

## OCR-only pipeline (no benchmark)

If you just want OCR (not a method comparison), use the standalone script — it accepts any folder and writes a searchable PDF + TXT + DOCX + ODT:

```bash
python scripts/ocr_folder.py /path/to/scans/ -o ~/OCR/
```

End-to-end recipes for popular scanners (NAPS2, VueScan, ScanSnap, HP Smart, Apple Image Capture, Windows Fax & Scan, SANE, mobile apps) live in [docs/PIPELINE.md](docs/PIPELINE.md).

## Auto-OCR after scan

Three paths, pick whichever fits the workstation. All of them ultimately call
`python benchmark.py` against `input/<doc_folder>/`, so the OCR pipeline is the
same — only the trigger changes.

### Path 1 — Watch folder (cross-platform, recommended)

The benchmark itself can run as a daemon: drop scanned pages into
`input/<doc_folder>/`, wait `--watch-debounce` seconds of quiet, and OCR fires
automatically for just that folder.

```bash
# Production daemon: only OCRmyPDF, no CSV noise, 5s debounce (default)
python benchmark.py --mode production --watch

# Compare daemon: all 4 sensible methods, 10s debounce (slow scanners)
python benchmark.py --mode compare --watch --watch-debounce 10
```

Works with any scanner that saves to `input/<doc_name>/page-NNN.{jpg,pdf}` —
NAPS2, Apple Image Capture, Windows Fax & Scan, a phone-scanner app syncing
via iCloud/Dropbox, even `cp` from CLI. Per-doc output lands in
`output/<method>/<doc_name>.{txt,docx,odt}` as soon as OCR finishes.

To run as a background service:

| OS | How |
|---|---|
| macOS | `launchd` plist under `~/Library/LaunchAgents/` invoking the watch command. `brew services` if you wrap it as a formula. |
| Windows | `nssm install ocr-watch "C:\path\to\.venv\Scripts\python.exe" "C:\path\to\benchmark.py --mode production --watch"` |
| Both | `tmux new -d -s ocr 'python benchmark.py --mode production --watch'` for a quick persistent session |

### Path 2 — NAPS2 post-scan command (GUI scanner, manual trigger)

NAPS2 (Windows + macOS) can run a shell command after every saved scan. Setup:

1. NAPS2 → **Profiles → Edit** → **OCR** tab: leave OCR **off** (we'll handle OCR externally; NAPS2's bundled Tesseract 5.2 is older than the system one).
2. NAPS2 → **Settings → After saving** → **Run command**:
   - **Command**: `python` (Windows: full path, e.g. `C:\path\to\.venv\Scripts\python.exe`)
   - **Arguments**:
     ```
     C:\path\to\benchmark.py --mode production --doc "$(folder)"
     ```
     where `$(folder)` is the NAPS2 placeholder for the save folder name. On macOS use forward slashes.
3. NAPS2 → **Save profile**: set the output path to `input/<doc_name>/page-$(n).jpg` so each scan lands in the right per-doc folder.

After scanning, NAPS2 saves the JPG/PDF then immediately calls the OCR. No daemon
required, no clicks beyond "Scan".

### Path 3 — Native OS folder hooks (no extra tools)

Use the OS file-event system directly to trigger the same CLI.

**macOS — Folder Action (built-in, GUI)**
1. Right-click `input/` in Finder → **Services → Folder Actions Setup…**
2. Attach action: **Run AppleScript** with body:
   ```applescript
   on adding folder items to thisFolder after receiving addedItems
     do shell script "cd /Users/<you>/projects/ocr-test && \
       source .venv/bin/activate && \
       python benchmark.py --mode production --doc \"" & ¬
       (name of (info for thisFolder)) & "\""
   end adding folder items to
   ```
3. Drop scans into `input/<doc>/` from Image Capture (macOS built-in) — Folder Action fires the OCR.

Equivalent CLI alternative: `fswatch` (`brew install fswatch`) piped into a shell loop —
useful if you want it scriptable and version-controllable:
```bash
fswatch -e ".*" -i "\\.(jpg|jpeg|png|pdf)$" -r input/ | \
  while read path; do
    doc=$(basename "$(dirname "$path")")
    python benchmark.py --mode production --doc "$doc"
  done
```

**Windows — Task Scheduler + FileSystemWatcher (PowerShell)**

Save as `scripts/watch.ps1`:
```powershell
$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = "C:\path\to\ocr-test\input"
$watcher.IncludeSubdirectories = $true
$watcher.Filter = "*.*"
$watcher.EnableRaisingEvents = $true
Register-ObjectEvent $watcher Created -Action {
    $f = $Event.SourceEventArgs.FullPath
    if ($f -match '\.(jpg|jpeg|png|pdf)$' -and -not (Split-Path -Leaf $f).StartsWith('.')) {
        Start-Sleep 5  # debounce
        $doc = Split-Path -Leaf (Split-Path -Parent $f)
        & "C:\path\to\ocr-test\.venv\Scripts\python.exe" `
           "C:\path\to\ocr-test\benchmark.py" --mode production --doc $doc
    }
}
while ($true) { Start-Sleep 60 }
```
Run via Task Scheduler at logon (Action: `powershell.exe -File C:\path\to\watch.ps1`).

**Why Path 1 is recommended over Path 3:** the built-in `--watch` already
handles debounce, doc-level routing, multi-file scans of the same document,
and survives both OS-es with the same syntax. Path 3 only wins if you cannot
keep a Python process resident (e.g. shared kiosk).

### Choosing a path

| Scenario | Path |
|---|---|
| Single workstation, you control it | **Path 1** (watch daemon) |
| Hospital / lab kiosk with NAPS2 already in use | **Path 2** (NAPS2 hook) |
| Locked-down Windows where you can't run resident Python | **Path 3** (Task Scheduler) |
| Mixed: scanning from phone via iCloud Drive | **Path 1** pointing at the iCloud folder |

## Metrics

When `ground-truth-txt/<doc_name>.txt` exists, each run is scored on:

- `cer` — character error rate (jiwer)
- `wer` — word error rate
- `diacritic_cer` — CER over Polish diacritics only (ąćęłńóśźż + uppercase). This is the
  known failure mode of neural OCRs on Polish — score it separately.
- `number_accuracy` — fraction of source numbers found verbatim in the OCR output
- `seconds`, `peak_rss_mb` — performance

Without ground-truth, only performance metrics are recorded (the artifacts are still
produced for manual review).

### Ground-truth for this dataset

Pre-extracted from searchable source PDFs:

- `COVID-19 vs Szczepienia - JPG` ✓ (32 KB text layer, clean)
- `Wydział Psychologii w Wrocławiu_Standardy pisania w pracy dyplomowej` ✓ (22 KB)
- Other folders: no ground-truth (image-only sources or corrupted text layer). They run
  for performance + manual-review artifacts only.

To add ground-truth for another doc: put `ground-truth-txt/<folder_name>.txt` next to the
existing ones.

## Results

`python benchmark.py --mode full` on Linux 6.17 x86_64, Python 3.12.3, Tesseract 5.3.4,
CPU-only. Lower is better for CER/WER/diacritic_cer; higher is better for number_accuracy.

**COVID-19 vs Szczepienia** (22 pages, scanned thesis, mixed body + figures)

| Method            | CER    | WER    | diacritic_cer | number_accuracy | seconds | peak RSS |
|-------------------|--------|--------|---------------|-----------------|---------|----------|
| `ocrmypdf`        | **0.027** | **0.155** | 0.009         | 0.902           | **11.9** | 555 MB  |
| `tess_best_psm4`  | 0.029  | 0.157  | **0.003**     | 0.922           | 16.7    | **156 MB** |
| `tess_best_psm1`  | 0.029  | 0.159  | 0.004         | 0.898           | 21.0    | **130 MB** |
| `tess_fast_psm1`  | 0.029  | 0.156  | 0.010         | 0.906           | 18.5    | **156 MB** |
| `tess_best_psm6`  | 0.031  | 0.202  | 0.005         | 0.941           | 16.4    | **156 MB** |
| `rapidocr`        | 0.128  | 0.371  | 0.431         | **0.996**       | 20.8    | 849 MB   |

**Wydział Psychologii — Standardy pisania pracy dyplomowej** (11 pages, born-digital style guide)

| Method            | CER    | WER    | diacritic_cer | number_accuracy | seconds | peak RSS |
|-------------------|--------|--------|---------------|-----------------|---------|----------|
| `ocrmypdf`        | **0.063** | 0.149  | 0.019         | 0.993           | **7.8** | 1092 MB  |
| `tess_best_psm1`  | 0.064  | 0.152  | **0.013**     | **1.000**       | 13.2    | 640 MB   |
| `tess_best_psm4`  | 0.064  | 0.155  | **0.013**     | **1.000**       | 10.9    | 640 MB   |
| `tess_fast_psm1`  | 0.065  | **0.151** | **0.013**  | 0.993           | 11.4    | 640 MB   |
| `rapidocr`        | 0.079  | 0.310  | 0.396         | **1.000**       | 11.4    | 1116 MB  |
| `tess_best_psm6`  | 0.091  | 0.206  | 0.033         | 0.986           | 10.4    | 640 MB   |

### Takeaways

- **OCRmyPDF wins on raw CER/WER and is the fastest.** Deskew + clean + rotate pays off
  on scanned input, even with Tesseract under the hood. Recommended default.
- **Tesseract `tessdata_best` PSM 1/4 is the winner on Polish diacritics**
  (`diacritic_cer` ≤ 1.3 %). PSM 6 (uniform block) is the worst on layout-heavy pages —
  do not use it for prose.
- **`tessdata_fast` matches `tessdata_best` on CER** but loses ~2× on diacritic accuracy
  (1.0 % vs 0.3–0.4 %). Use only when speed matters more than diacritics.
- **RapidOCR is the wrong tool for Polish prose**: 13 % CER and 40 %+ diacritic error on
  both docs, despite using the PP-OCRv5 Latin recognizer. It does, however, recover numbers
  almost perfectly (99.6–100 %) — keep it in mind for numeric forms / receipts where
  diacritics don't matter.
- Memory: Tesseract methods stay under 700 MB; OCRmyPDF and RapidOCR climb past 1 GB on the
  longer doc. None of this is a problem on a modern laptop.

Raw rows live in `output/results.csv` after each run.

## Folder layout

```
ocr-test/
├── benchmark.py
├── requirements.txt
├── scripts/
│   ├── setup_macos.sh
│   └── setup_windows.ps1
├── input/                  # N files per folder → one document
├── ground-truth/           # original source PDFs (kept for reference)
├── ground-truth-txt/       # pre-extracted reference text
└── output/
    ├── results.csv         # one row per (method × doc)
    └── <method>/<doc>.{txt,docx,odt}
```

## Environment variables

| Var                  | Purpose                                                   |
| -------------------- | --------------------------------------------------------- |
| `TESSERACT_CMD`      | Full path to `tesseract` / `tesseract.exe` (Windows fix)  |
| `TESSDATA_BEST_DIR`  | Override directory holding `pol.traineddata` (best)       |
| `TESSDATA_FAST_DIR`  | Override directory holding `pol.traineddata` (fast)       |

## Cross-platform notes

- macOS: Apple Silicon and Intel both fine. `ocrmypdf` uses bundled Ghostscript from Homebrew.
- Windows: install Tesseract from UB Mannheim (5.5.x), Poppler from oschwartz10612, Ghostscript from Artifex. The setup script does this via `winget` when available.
- Both: everything runs offline once installed. No telemetry, no model downloads at runtime
  beyond first-run RapidOCR model cache (pre-stage by running `--doctor` once on a connected
  machine, then copy `~/.cache/rapidocr/` or `%LOCALAPPDATA%\rapidocr\` to the air-gapped machine).
