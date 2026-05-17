# Setup for Windows 10/11. Run from PowerShell (no admin required for venv/pip).
# Tesseract / Poppler / Ghostscript need to be installed once; this script bootstraps
# them via winget when available, otherwise prints download links.
$ErrorActionPreference = "Stop"

Set-Location (Split-Path $PSScriptRoot -Parent)

function Test-Cmd($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

Write-Host "[1/6] Checking / installing system tools..."
if (Test-Cmd "winget") {
  if (-not (Test-Cmd "tesseract")) {
    Write-Host "  installing Tesseract (UB Mannheim)..."
    winget install --silent --accept-package-agreements --accept-source-agreements UB-Mannheim.TesseractOCR
  }
  if (-not (Test-Cmd "pdftotext")) {
    Write-Host "  installing Poppler (oschwartz10612 build)..."
    winget install --silent --accept-package-agreements --accept-source-agreements oschwartz10612.Poppler
  }
  if (-not (Test-Cmd "gswin64c")) {
    Write-Host "  installing Ghostscript..."
    winget install --silent --accept-package-agreements --accept-source-agreements ArtifexSoftware.GhostScript
  }
} else {
  Write-Host "winget not available — install manually:" -ForegroundColor Yellow
  Write-Host "  Tesseract 5.5.x:  https://digi.bib.uni-mannheim.de/tesseract/"
  Write-Host "  Poppler:          https://github.com/oschwartz10612/poppler-windows/releases/latest"
  Write-Host "  Ghostscript:      https://www.ghostscript.com/releases/gsdnld.html"
  Read-Host "Press Enter once installed and added to PATH"
}

Write-Host "[2/6] Creating Python venv (.venv)..."
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip wheel

Write-Host "[3/6] Installing Python packages..."
pip install -r requirements.txt

Write-Host "[4/6] Locating Tesseract..."
$tess = (Get-Command tesseract -ErrorAction SilentlyContinue).Source
if (-not $tess) {
  $candidates = @(
    "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
    "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
  )
  $tess = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($tess) {
  Write-Host "  Tesseract: $tess"
  [Environment]::SetEnvironmentVariable("TESSERACT_CMD", $tess, "User")
} else {
  Write-Host "  WARNING: tesseract.exe not found. Set `$env:TESSERACT_CMD before running." -ForegroundColor Yellow
}

Write-Host "[5/6] Pre-staging tessdata_best / tessdata_fast for Polish..."
$bestDir = Join-Path $HOME "tessdata_best"
$fastDir = Join-Path $HOME "tessdata_fast"
New-Item -ItemType Directory -Force -Path $bestDir, $fastDir | Out-Null
foreach ($f in @("pol.traineddata", "osd.traineddata", "eng.traineddata")) {
  $target = Join-Path $bestDir $f
  if (-not (Test-Path $target)) {
    Write-Host "  downloading best/$f"
    Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_best/raw/main/$f" -OutFile $target
  }
  $target = Join-Path $fastDir $f
  if (-not (Test-Path $target)) {
    Write-Host "  downloading fast/$f"
    Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$f" -OutFile $target
  }
}
[Environment]::SetEnvironmentVariable("TESSDATA_BEST_DIR", $bestDir, "User")
[Environment]::SetEnvironmentVariable("TESSDATA_FAST_DIR", $fastDir, "User")

Write-Host "[6/6] Done."
Write-Host ""
Write-Host "Open a NEW PowerShell window (so env vars are picked up), then run:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python benchmark.py --doctor"
Write-Host "  python benchmark.py"
