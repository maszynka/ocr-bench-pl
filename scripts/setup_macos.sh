#!/usr/bin/env bash
# Setup for macOS (Homebrew). Run once per machine.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Install from https://brew.sh and re-run." >&2
  exit 1
fi

echo "[1/5] Installing system deps (tesseract, tesseract-lang, poppler, ghostscript)..."
brew install tesseract tesseract-lang poppler ghostscript unpaper

echo "[2/5] Creating Python venv (.venv)..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel

echo "[3/5] Installing Python packages..."
pip install -r requirements.txt

echo "[4/5] Pre-staging tessdata_best Polish (optional, improves accuracy)..."
TESS_PREFIX="$(brew --prefix tesseract)"
SHARE_DIR="$TESS_PREFIX/share/tessdata"
BEST_DIR="$HOME/tessdata_best"
FAST_DIR="$HOME/tessdata_fast"
mkdir -p "$BEST_DIR" "$FAST_DIR"
for f in pol.traineddata osd.traineddata eng.traineddata; do
  if [ ! -f "$BEST_DIR/$f" ]; then
    echo "  downloading best/$f"
    curl -sSL "https://github.com/tesseract-ocr/tessdata_best/raw/main/$f" -o "$BEST_DIR/$f"
  fi
  if [ ! -f "$FAST_DIR/$f" ]; then
    echo "  downloading fast/$f"
    curl -sSL "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$f" -o "$FAST_DIR/$f"
  fi
done

echo "[5/5] Done."
cat <<EOF

Activate the venv and run the doctor:
  source .venv/bin/activate
  export TESSDATA_BEST_DIR="$BEST_DIR"
  export TESSDATA_FAST_DIR="$FAST_DIR"
  python benchmark.py --doctor

Then run the full benchmark:
  python benchmark.py

Optional smoke test (first 2 pages per doc):
  python benchmark.py --max-pages 2
EOF
