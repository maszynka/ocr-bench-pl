# End-to-end OCR pipeline — scanner → searchable PDF / DOCX / ODT

This guide shows how to bolt the OCR step onto a real scanning workflow. The OCR
itself is always the same — OCRmyPDF + Tesseract `tessdata_best`, the combo that
won the benchmark (see [README → Results](../README.md#results)). What changes is
**how scans get into the pipeline**.

Three layers:

1. **OCR engine** — `scripts/ocr_folder.py` (standalone) or `benchmark.py --mode production` (uses the `input/<doc>/` layout).
2. **Trigger** — what fires the OCR: manual CLI, scanner's post-save hook, OS folder watcher, or our built-in `--watch` daemon.
3. **Source** — the scanner: NAPS2, VueScan, vendor utilities (HP/Brother/Epson/ScanSnap), Apple Image Capture, Windows Fax & Scan, Linux SANE, or a phone app syncing via iCloud / Dropbox / Google Drive.

You only need to pick one option per layer. The most common combos are at the bottom — [Recipes](#recipes-pick-one).

---

## 1. The OCR engine

### Option A — `scripts/ocr_folder.py` (standalone, recommended for ad-hoc OCR)

Point it at any folder; get back a searchable PDF + plain text + DOCX + ODT.

```bash
# Single doc
python scripts/ocr_folder.py /path/to/scans/ -o ~/OCR/

# Bulk: every immediate subfolder is its own document
for d in ~/Scans/*/; do
  python scripts/ocr_folder.py "$d" -o ~/OCR/
done

# Mixed Polish + English (forms, contracts with English boilerplate)
python scripts/ocr_folder.py ~/Scans/contract/ -o ~/OCR/ --lang pol+eng
```

Outputs land at `<output_dir>/<folder_name>.{pdf,txt,docx,odt}`. The PDF is the original images with a text layer on top — fully searchable and copy-paste-able in any PDF reader.

### Option B — `benchmark.py --mode production --doc <name>` (uses repo layout)

Drop scans into `input/<doc_name>/`, then:

```bash
python benchmark.py --mode production --doc "<doc_name>"
# → output/ocrmypdf/<doc_name>.{txt,docx,odt}
```

Use this when you want everything organized inside the repo and don't need the searchable PDF. The `--mode production` preset disables CSV metric writes so it stays clean.

### Option C — `benchmark.py --watch` (daemon, hands-free)

Resident process that watches `input/` and auto-OCRs each folder whenever new files arrive. See [README → Path 1](../README.md#path-1--watch-folder-cross-platform-recommended).

---

## 2. Connecting popular scanning software

The patterns below all end with "run a command after every scan". Wire that command to one of the OCR engines above and you're done.

### NAPS2 (Windows, macOS, Linux — free, open source)

Best general-purpose scanner UI. Cross-platform, supports both flatbed and ADF, has a built-in post-save command.

1. **Profiles → Edit → OCR** — disable NAPS2's bundled Tesseract (it's older than the one in this repo's venv).
2. **Profiles → Edit → Save** — set output to `~/Scans/<doc>/page-$(n).jpg` (or PDF).
3. **Settings → After saving → Run command**:
   - **Command**: `python` (Windows: full path to the venv's python.exe)
   - **Arguments**:
     ```
     /path/to/ocr-bench-pl/scripts/ocr_folder.py "$(folder)" -o ~/OCR/
     ```
     `$(folder)` is the NAPS2 placeholder for the save folder.

After each scan: page hits disk → NAPS2 fires the command → ocr_folder.py produces the searchable PDF in `~/OCR/`. Zero clicks.

### VueScan (Windows, macOS, Linux — commercial, ~$40)

Powerful driver layer for older / odd scanners that vendors no longer support. Has a post-scan command too.

1. **Output** tab → set **JPEG file** to `~/Scans/[doc]/page+.jpg` (the `+` is VueScan's auto-increment).
2. **Prefs** tab → **External viewer** field: enter the full command that should run after each save. Easiest: a tiny wrapper script `~/bin/ocr-trigger.sh`:
   ```bash
   #!/usr/bin/env bash
   FOLDER="$(dirname "$1")"
   /path/to/ocr-bench-pl/.venv/bin/python \
     /path/to/ocr-bench-pl/scripts/ocr_folder.py "$FOLDER" -o ~/OCR/
   ```
   Then in VueScan: **External viewer**: `~/bin/ocr-trigger.sh` (or full path).
3. **Output → Default folder**: `~/Scans/`.

Tip: turn OFF VueScan's own OCR — its Tesseract version is older too.

### Fujitsu / Ricoh ScanSnap (ScanSnap Home, Windows / macOS)

Popular fast document scanners (S1300i, iX1300/1400/1500/1600, etc.). ScanSnap Home has **Profiles** with post-scan actions.

1. **Profile** → **Application** → set to "Scan to Folder".
2. **Save destination**: `~/Scans/scansnap-inbox/`.
3. **Profile** → **Quick Menu → Application** → add a custom application that points to your trigger script:
   ```bash
   #!/usr/bin/env bash
   # Group everything that came from this scan into one timestamped doc.
   STAMP=$(date +%Y%m%d-%H%M%S)
   DEST=~/Scans/scansnap-$STAMP/
   mkdir -p "$DEST"
   mv ~/Scans/scansnap-inbox/* "$DEST"
   /path/to/ocr-bench-pl/.venv/bin/python \
     /path/to/ocr-bench-pl/scripts/ocr_folder.py "$DEST" -o ~/OCR/
   ```
4. Enable **Continuous scanning** if you want multi-page docs to land in one folder before OCR fires.

Alternative: just have ScanSnap save into `input/scansnap/` and run our `--watch` daemon. No wrapper script needed.

### HP Smart / Brother iPrint&Scan / Epson Scan 2 (vendor utilities)

These typically save to a fixed folder (often `~/Documents/Scans/` or `~/Pictures/`). They don't expose a post-scan command, so use a **folder watcher** instead — either our `--watch` daemon or an OS hook.

```bash
# Point our daemon at the vendor's save folder via a symlink:
ln -s ~/Documents/Scans/HPSmart input/hp-smart
python benchmark.py --mode production --watch
```

For mode flexibility (DOCX/ODT/PDF all in one shot), use `ocr_folder.py` via `fswatch` (macOS / Linux) or PowerShell `FileSystemWatcher` (Windows). See [README → Path 3](../README.md#path-3--native-os-folder-hooks-no-extra-tools).

### Apple Image Capture (macOS, built-in)

Right on macOS, no install needed. Works with any scanner that has a TWAIN / ICA driver — most do.

1. Open **Image Capture** → select scanner.
2. **Scan To**: pick a per-doc folder under `~/Scans/<doc>/`.
3. **Format**: JPEG (or PDF for multi-page).
4. After scanning, run:
   ```bash
   python scripts/ocr_folder.py ~/Scans/<doc>/ -o ~/OCR/
   ```

For automation, attach a **Folder Action** to `~/Scans/` — see [README → Path 3 → macOS](../README.md#path-3--native-os-folder-hooks-no-extra-tools).

### Windows Fax and Scan (built-in)

1. **New Scan** → set **File type** to JPG.
2. Scanned files land in `Documents\Scanned Documents\`.
3. Run the OCR with the venv's python:
   ```powershell
   & C:\path\to\ocr-bench-pl\.venv\Scripts\python.exe `
     C:\path\to\ocr-bench-pl\scripts\ocr_folder.py `
     "$HOME\Documents\Scanned Documents" -o "$HOME\OCR"
   ```

For automation, use the Task Scheduler + `FileSystemWatcher` snippet in [README → Path 3 → Windows](../README.md#path-3--native-os-folder-hooks-no-extra-tools).

### Linux: SANE / scanimage (any TWAIN-less Linux setup)

Cheapest, scriptable. Works headless.

```bash
sudo apt install sane-utils
scanimage -L                                  # list devices
DOC=~/Scans/$(date +%Y%m%d-%H%M%S)
mkdir -p "$DOC"
scanimage --format=jpeg --resolution 300 \
  --batch="$DOC/page-%03d.jpg" --batch-prompt   # press Enter between sheets
python scripts/ocr_folder.py "$DOC" -o ~/OCR/
```

Wrap the last three lines in a shell function (`scan-doc <name>`) and you have a one-command pipeline.

### Mobile scanners (Adobe Scan, Microsoft Lens, Genius Scan, iOS Notes)

These produce PDFs (or JPGs) on your phone. Two paths:

1. **Sync via iCloud Drive / Dropbox / Google Drive** — set the app's save destination to a sync folder. On the desktop, point `--watch` (or an OS hook) at the synced folder. The OCR fires as soon as the sync completes.
   ```bash
   # macOS example: Adobe Scan → save to iCloud Drive → ocr-bench-pl/input/mobile-scans/
   ln -s "$HOME/Library/Mobile Documents/com~apple~CloudDocs/AdobeScan" input/mobile-scans
   python benchmark.py --mode production --watch --watch-debounce 15
   ```
   Use a longer debounce (10–30 s) for cloud sync — files arrive in spurts.

2. **AirDrop / email to self** — manual but no infra. Drop the PDF into any folder and run:
   ```bash
   python scripts/ocr_folder.py ~/Downloads/scan-folder/ -o ~/OCR/
   ```

iOS Notes (built-in "Scan Documents") produces searchable PDFs *already*, but the OCR is Apple's and Polish accuracy is mediocre — re-run through this pipeline for diacritics.

---

## 3. Running it as a background service

If you went with `benchmark.py --watch` (or wrapped `ocr_folder.py` in a loop), you probably want it to survive logouts.

| OS | How |
|---|---|
| Linux (systemd user service) | `~/.config/systemd/user/ocr-watch.service` with `ExecStart=/path/to/.venv/bin/python /path/to/benchmark.py --mode production --watch`; then `systemctl --user enable --now ocr-watch`. |
| macOS (launchd) | `~/Library/LaunchAgents/com.local.ocr-watch.plist` with `<ProgramArguments>` invoking the same command; `launchctl load …`. |
| Windows | `nssm install ocr-watch "C:\…\.venv\Scripts\python.exe" "C:\…\benchmark.py --mode production --watch"`. |
| Any | `tmux new -d -s ocr 'python benchmark.py --mode production --watch'` — quick & dirty, dies on reboot. |

---

## Recipes (pick one)

| You have… | Run this |
|---|---|
| A folder of JPGs from any source, want one searchable PDF + DOCX | `python scripts/ocr_folder.py /path/to/folder -o ~/OCR/` |
| NAPS2 already installed | NAPS2 post-save command → `ocr_folder.py "$(folder)" -o ~/OCR/` |
| ScanSnap, no scripting taste | ScanSnap Home → "Scan to Folder" into `input/scansnap/` + `benchmark.py --mode production --watch` |
| Phone-only workflow | Adobe Scan → iCloud/Dropbox → `--watch` daemon on the desktop pointed at the sync folder |
| One-off bulk OCR of an archive | `for d in ~/Archive/*/; do python scripts/ocr_folder.py "$d" -o ~/OCR/; done` |
| Linux headless + USB scanner | `scanimage --batch … && python scripts/ocr_folder.py …` (wrap in a shell function) |

All recipes use the same OCR engine, so you can pick based on the trigger that fits your environment and switch later without re-OCRing anything.
