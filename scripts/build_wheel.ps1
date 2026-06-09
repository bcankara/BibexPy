#requires -Version 5.1
<#
.SYNOPSIS
  BibexPy v2 — pip wheel build (Windows).

.DESCRIPTION
  Üç parçayı python_pkg/src altına kopyalar ve tek bir self-contained wheel üretir:
    • apps/web/out/        → src/bibexpy/_web/      (Next.js static export)
    • apps/api/{...}       → src/bibexpy/_server/   (FastAPI uygulaması)
    • packages/bibex_core/ → src/bibex_core/        (çekirdek modüller)

  Kaynak gerçeği apps/ ve packages/ içinde kalır; buradaki kopyalar gitignore'lu
  build artıklarıdır. Çıktı: python_pkg/dist/bibexpy-*.whl

.EXAMPLE
  pwsh scripts/build_wheel.ps1
#>
$ErrorActionPreference = "Stop"

# --- Yollar (script scripts/ altında; repo kökü bir üst dizin) ---
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
    # robocopy: 0-7 başarı, >=8 hata
    if ($LASTEXITCODE -ge 8) { throw "robocopy hata ($LASTEXITCODE): $from -> $to" }
    $global:LASTEXITCODE = 0
}

Write-Host "==> 1/6  Frontend static export (npm run build:static)" -ForegroundColor Cyan
Push-Location $Web
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "     node_modules yok — npm install" -ForegroundColor DarkGray
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install başarısız" }
    }
    npm run build:static
    if ($LASTEXITCODE -ne 0) { throw "npm run build:static başarısız" }
} finally { Pop-Location }

$Out = Join-Path $Web "out"
if (-not (Test-Path (Join-Path $Out "index.html"))) {
    throw "apps/web/out/index.html üretilmedi — static export başarısız olmuş olabilir"
}

Write-Host "==> 2/6  Eski kopyaları temizle" -ForegroundColor Cyan
foreach ($d in @($DestWeb, $DestServer, $DestCore)) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}

Write-Host "==> 3/6  Frontend kopyala -> _web" -ForegroundColor Cyan
Invoke-Robocopy $Out $DestWeb @()

Write-Host "==> 4/6  Sunucu kopyala -> _server" -ForegroundColor Cyan
New-Item -ItemType Directory -Force $DestServer | Out-Null
Copy-Item (Join-Path $Api "main.py")   $DestServer
Copy-Item (Join-Path $Api "config.py") $DestServer
foreach ($sub in @("routers", "services", "models", "jobs")) {
    Invoke-Robocopy (Join-Path $Api $sub) (Join-Path $DestServer $sub) @()
}
# _server'i Python alt-paketi yap — setuptools .py dosyalarini SADECE paket
# olarak keşfederse wheel'e dahil eder (package-data ile .py guvenilir tasinmaz).
$initContent = @'
"""BibexPy gomulu FastAPI sunucusu (build-time kopya). Runtime sys.path e eklenir."""
'@
Set-Content -Path (Join-Path $DestServer "__init__.py") -Value $initContent -Encoding UTF8

Write-Host "==> 5/6  bibex_core kopyala" -ForegroundColor Cyan
Invoke-Robocopy $Core $DestCore @()
# Vendored kopyada kendi pyproject'ine gerek yok (python_pkg/pyproject.toml kullanılır)
$nestedToml = Join-Path $DestCore "pyproject.toml"
if (Test-Path $nestedToml) { Remove-Item -Force $nestedToml }

Write-Host "==> 6/6  Wheel build (python -m build)" -ForegroundColor Cyan
Push-Location $Pkg
try {
    python -m pip install --quiet --upgrade build
    if ($LASTEXITCODE -ne 0) { throw "'build' paketi kurulamadı" }
    foreach ($d in @("dist", "build")) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
    Get-ChildItem -Filter "*.egg-info" -Recurse | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # --wheel: sdist ara adımını atla. _server/_web build-time kopyaları git'te
    # izlenmediğinden sdist'e girmez; wheel'i doğrudan kaynak ağacından kurmak hepsini dahil eder.
    python -m build --wheel
    if ($LASTEXITCODE -ne 0) { throw "python -m build başarısız" }
} finally { Pop-Location }

Write-Host ""
Write-Host "BASARILI. Uretilen paketler:" -ForegroundColor Green
Get-ChildItem (Join-Path $Pkg "dist") | ForEach-Object { Write-Host "  $($_.FullName)" -ForegroundColor Green }
Write-Host ""
Write-Host "Kurulum testi:  pip install `"$(Join-Path $Pkg 'dist')\bibexpy-2.0.0-py3-none-any.whl`"" -ForegroundColor DarkGray
Write-Host "Yayinlama:      python -m twine upload python_pkg/dist/*" -ForegroundColor DarkGray
