#requires -Version 5.1
<#
.SYNOPSIS
  BibexPy v2 — pip wheel build (Windows).

.DESCRIPTION
  Copies three parts under python_pkg/src and produces a single self-contained wheel:
    • apps/web/out/        → src/bibexpy/_web/      (Next.js static export)
    • apps/api/{...}       → src/bibexpy/_server/   (FastAPI application)
    • packages/bibex_core/ → src/bibex_core/        (core modules)

  The source of truth stays in apps/ and packages/; the copies here are
  gitignored build artifacts. Output: python_pkg/dist/bibexpy-*.whl

.EXAMPLE
  pwsh scripts/build_wheel.ps1
#>
$ErrorActionPreference = "Stop"

# --- Paths (the script lives under scripts/; the repo root is one directory up) ---
$Root       = Split-Path -Parent $PSScriptRoot
$Web        = Join-Path $Root "apps\web"
$Api        = Join-Path $Root "apps\api"
$Core       = Join-Path $Root "packages\bibex_core"
$Pkg        = Join-Path $Root "python_pkg"
$Src        = Join-Path $Pkg  "src"
$DestWeb    = Join-Path $Src  "bibexpy\_web"
$DestServer = Join-Path $Src  "bibexpy\_server"
$DestCore   = Join-Path $Src  "bibex_core"

function Invoke-Robocopy($from, $to, [string[]]$extra) {
    $base = @($from, $to, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XD", "__pycache__", "/XF", "*.pyc", "*.pyo")
    robocopy @base @extra | Out-Null
    # robocopy: 0-7 success, >=8 error
    if ($LASTEXITCODE -ge 8) { throw "robocopy error ($LASTEXITCODE): $from -> $to" }
    $global:LASTEXITCODE = 0
}

Write-Host "==> 1/6  Frontend static export (npm run build:static)" -ForegroundColor Cyan
Push-Location $Web
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "     node_modules missing — npm install" -ForegroundColor DarkGray
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    }
    npm run build:static
    if ($LASTEXITCODE -ne 0) { throw "npm run build:static failed" }
} finally { Pop-Location }

$Out = Join-Path $Web "out"
if (-not (Test-Path (Join-Path $Out "index.html"))) {
    throw "apps/web/out/index.html was not produced — the static export may have failed"
}

Write-Host "==> 2/6  Clean old copies" -ForegroundColor Cyan
foreach ($d in @($DestWeb, $DestServer, $DestCore)) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}

Write-Host "==> 3/6  Copy frontend -> _web" -ForegroundColor Cyan
Invoke-Robocopy $Out $DestWeb @()

Write-Host "==> 4/6  Copy server -> _server" -ForegroundColor Cyan
New-Item -ItemType Directory -Force $DestServer | Out-Null
Copy-Item (Join-Path $Api "main.py")   $DestServer
Copy-Item (Join-Path $Api "config.py") $DestServer
foreach ($sub in @("routers", "services", "models", "jobs")) {
    Invoke-Robocopy (Join-Path $Api $sub) (Join-Path $DestServer $sub) @()
}
# Make _server a Python sub-package — setuptools includes .py files in the wheel ONLY
# if it discovers them as a package (.py files are not reliably carried via package-data).
$initContent = @'
"""BibexPy gomulu FastAPI sunucusu (build-time kopya). Runtime sys.path e eklenir."""
'@
Set-Content -Path (Join-Path $DestServer "__init__.py") -Value $initContent -Encoding UTF8

Write-Host "==> 5/6  Copy bibex_core" -ForegroundColor Cyan
Invoke-Robocopy $Core $DestCore @()
# The vendored copy does not need its own pyproject (python_pkg/pyproject.toml is used)
$nestedToml = Join-Path $DestCore "pyproject.toml"
if (Test-Path $nestedToml) { Remove-Item -Force $nestedToml }

Write-Host "==> 6/6  Wheel build (python -m build)" -ForegroundColor Cyan
Push-Location $Pkg
try {
    python -m pip install --quiet --upgrade build
    if ($LASTEXITCODE -ne 0) { throw "could not install the 'build' package" }
    foreach ($d in @("dist", "build")) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
    Get-ChildItem -Filter "*.egg-info" -Recurse | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # --wheel: skip the intermediate sdist step. The _server/_web build-time copies are not
    # tracked in git, so they do not make it into the sdist; building the wheel directly
    # from the source tree includes them all.
    python -m build --wheel
    if ($LASTEXITCODE -ne 0) { throw "python -m build failed" }
} finally { Pop-Location }

Write-Host ""
Write-Host "SUCCESS. Generated packages:" -ForegroundColor Green
Get-ChildItem (Join-Path $Pkg "dist") | ForEach-Object { Write-Host "  $($_.FullName)" -ForegroundColor Green }
Write-Host ""
Write-Host "Install test:   pip install `"$(Join-Path $Pkg 'dist')\bibexpy-2.0.0-py3-none-any.whl`"" -ForegroundColor DarkGray
Write-Host "Publishing:     python -m twine upload python_pkg/dist/*" -ForegroundColor DarkGray
