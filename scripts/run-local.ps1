# One-command Windows bring-up: DB (docker) + engine (:8000) + console (:3005).
#
#   powershell -ExecutionPolicy Bypass -File scripts\run-local.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run-local.ps1 -IngestKeebs
#
# Prereqs (one-time): Docker Desktop running, Python 3.11+, Node 18+, git.
# Credentials: put the operator .env at engine\.env (the engine loads it itself).
# The console port is fixed at 3005 (operator's choice); engine stays on 8000.
#
# -IngestKeebs additionally pulls the real Keebs portfolio image branches and
# ingests them through the engine's VLM pipeline (needs the engine up + a live
# ANTHROPIC_API_KEY in engine\.env — tags are then produced by the real VLM).

param(
    [switch]$IngestKeebs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$Dsn = "postgresql://scalers:scalers@localhost:5432/scalers"

Write-Host "== 0/5 Latest code" -ForegroundColor Cyan
git pull origin main

Write-Host "== 1/5 Postgres (docker compose)" -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop is not running - start it first." -ForegroundColor Red; exit 1 }
Set-Location "$Root\infra"
docker compose up -d postgres redis
Set-Location $Root
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    docker exec scalers-postgres pg_isready -U scalers -d scalers -q *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ok) { Write-Host "Postgres did not become ready." -ForegroundColor Red; exit 1 }

Write-Host "== 2/5 Schema (infra\initdb, idempotent)" -ForegroundColor Cyan
Get-ChildItem "$Root\infra\initdb\*.sql" | Sort-Object Name | ForEach-Object {
    Get-Content $_.FullName -Raw | docker exec -i scalers-postgres psql -U scalers -d scalers -q
}

Write-Host "== 3/5 Engine deps + store bootstrap" -ForegroundColor Cyan
Set-Location "$Root\engine"
$env:ENGINE_DATABASE_URL = $Dsn
$env:STUDIO_TENANT_ID = "skindesign"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { pip install uv }
uv sync --extra postgres
uv run python bootstrap_db.py

Write-Host "== 4/5 Engine on :8000 (new window)" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$Root\engine'; " +
    "`$env:ENGINE_DATABASE_URL='$Dsn'; " +
    "`$env:STUDIO_TENANT_ID='skindesign'; " +
    "`$env:SUPERVISOR_PATROL_SECONDS='60'; " +
    "uv run uvicorn main:app --host 127.0.0.1 --port 8000"
)
$up = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/healthz" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $up) { Write-Host "Engine did not come up - check the engine window for the error." -ForegroundColor Red; exit 1 }
Write-Host "engine :8000 healthy" -ForegroundColor Green

Write-Host "== 5/5 Console on :3005 (new window)" -ForegroundColor Cyan
Set-Location "$Root\web"
if (-not (Test-Path "node_modules")) { npm install }
if (-not (Test-Path ".env.local")) {
    (Get-Content ".env.example" -Raw) `
        -replace "ladies8391", "skindesign" `
        -replace "http://127.0.0.1:8010", "http://127.0.0.1:8000" |
        Set-Content ".env.local"
}
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command", "cd '$Root\web'; npm run dev -- --port 3005"
)
Set-Location $Root

if ($IngestKeebs) {
    Write-Host "== extra: real Keebs portfolio ingest (VLM)" -ForegroundColor Cyan
    git fetch origin Cardano-max-patch-1 Cardano-max-patch-2
    New-Item -ItemType Directory -Force -Path "$Root\.inbound\keebs-real" | Out-Null
    foreach ($branch in @("Cardano-max-patch-1", "Cardano-max-patch-2")) {
        git ls-tree "origin/$branch" --name-only | Where-Object { $_ -match "\.(jpg|jpeg|png|mp4)$" } | ForEach-Object {
            git show "origin/${branch}:$_" | Set-Content -Path "$Root\.inbound\keebs-real\$_" -AsByteStream
        }
    }
    # Wait for the console window to be reachable is not needed; ingest talks to the engine.
    python "$Root\scripts\ingest_artwork_dir.py" "$Root\.inbound\keebs-real" Keebs --prompt "Real personal portfolio of Keebs (Skin Design Tattoo), July 2026"
}

Write-Host ""
Write-Host "READY:" -ForegroundColor Green
Write-Host "  Console  http://localhost:3005   (Voice / Agency / Review / Runs / Memory / Artists)"
Write-Host "  Engine   http://localhost:8000/healthz"
Write-Host "  Fleet    http://localhost:8000/studio/fleet"
Write-Host "Safety: tenant 'skindesign' is TEST-MODE - nothing sends to real customers without the allowlist/live authorization."
