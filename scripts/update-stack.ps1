# =============================================================================
#  Scalers — UPDATE + RESTART + SELF-VERIFY  (Windows / PowerShell)
#
#    powershell -ExecutionPolicy Bypass -File scripts\update-stack.ps1
#
#  Use this BEFORE a demo/meeting. It pulls the latest verified code, runs the
#  new artwork backfill (without it posts come out with NO image), restarts the
#  engine (:8000) and console (:3005) cleanly, and then SANITY-CHECKS the whole
#  new flow so you walk in knowing it works.
#
#  Prereqs (unchanged): Docker Desktop running, Python 3.11+, Node 18+, git,
#  and your operator creds already at engine\.env. Postgres/schema are assumed
#  already set up by scripts\run-local.ps1 at least once — if this is a brand
#  new machine, run run-local.ps1 first, then this.
# =============================================================================

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$Dsn      = "postgresql://scalers:scalers@localhost:5432/scalers"
$Tenant   = "skindesign"           # NOTE: no trailing space (a stray space empties the artwork library)
$EnginePort  = 8000
$ConsolePort = 3005

function Stop-Port([int]$Port) {
    try {
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    } catch { }
}

Write-Host "== 1/6  Stop the old engine + console" -ForegroundColor Cyan
Stop-Port $EnginePort
Stop-Port $ConsolePort
Start-Sleep -Seconds 2
Write-Host "   old windows stopped (:$EnginePort, :$ConsolePort)" -ForegroundColor Green

Write-Host "== 2/6  Pull the latest verified code (origin/main)" -ForegroundColor Cyan
git fetch origin main
git checkout main
git pull origin main
$Head = (git rev-parse --short HEAD)
Write-Host "   now at main @ $Head" -ForegroundColor Green

Write-Host "== 3/6  Postgres up + schema (idempotent)" -ForegroundColor Cyan
if ((docker ps -a --format "{{.Names}}") -contains "scalers-postgres") {
    docker start scalers-postgres | Out-Null
    if ((docker ps -a --format "{{.Names}}") -contains "scalers-redis") { docker start scalers-redis | Out-Null }
} else {
    Write-Host "   scalers-postgres container not found — run scripts\run-local.ps1 first." -ForegroundColor Red
    exit 1
}
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    docker exec scalers-postgres pg_isready -U scalers -d scalers -q *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ok) { Write-Host "Postgres did not become ready." -ForegroundColor Red; exit 1 }
Get-ChildItem "$Root\infra\initdb\*.sql" | Sort-Object Name | ForEach-Object {
    Get-Content $_.FullName -Raw | docker exec -i scalers-postgres psql -U scalers -d scalers -q
}
Write-Host "   postgres ready + schema applied" -ForegroundColor Green

Write-Host "== 4/6  Engine deps + artwork backfill (the NEW step)" -ForegroundColor Cyan
Set-Location "$Root\engine"
$env:ENGINE_DATABASE_URL = $Dsn
$env:STUDIO_TENANT_ID    = $Tenant
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { pip install uv }
uv sync --extra postgres
uv run python bootstrap_db.py
# CRITICAL new flow: copy each uploaded piece's VLM tags + summary + artifact link
# from context_artifacts ONTO the assets rows the artwork ranker reads. Without this
# a botanical brief finds no tags, ranks nothing, and the post ships with no image.
Write-Host "   backfilling artwork tags/summaries onto the ranker's rows..."
uv run python scripts\backfill_artwork_tags.py
Set-Location $Root
Write-Host "   engine deps synced + artwork backfilled" -ForegroundColor Green

Write-Host "== 5/6  Start engine (:$EnginePort) + console (:$ConsolePort) in new windows" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$Root\engine'; " +
    "`$env:ENGINE_DATABASE_URL='$Dsn'; " +
    "`$env:STUDIO_TENANT_ID='$Tenant'; " +          # set in PowerShell, so no cmd.exe trailing-space bug
    "`$env:SUPERVISOR_PATROL_SECONDS='60'; " +
    "`$env:ACTION_SCHEDULER_SECONDS='60'; " +
    "uv run uvicorn main:app --host 127.0.0.1 --port $EnginePort"
)
$up = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$EnginePort/healthz" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $up) { Write-Host "Engine did not come up — check the engine window." -ForegroundColor Red; exit 1 }
Set-Location "$Root\web"
if (-not (Test-Path "node_modules")) { npm install }
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command", "cd '$Root\web'; npm run dev -- --port $ConsolePort"
)
Set-Location $Root
Write-Host "   engine + console launching" -ForegroundColor Green

Write-Host "== 6/6  Self-verify the new flow (so you walk in knowing it works)" -ForegroundColor Cyan
$health = (Invoke-WebRequest -Uri "http://localhost:$EnginePort/healthz" -UseBasicParsing).Content | ConvertFrom-Json
Write-Host ("   tenant        : " + $health.studioTenant + "  (expect skindesign)")
Write-Host ("   model key     : " + $health.modelKeyPresent + "  (expect True)")
# Artwork tags actually present on the ranker's rows (the thing the backfill fixes)
$tagged = docker exec scalers-postgres psql -U scalers -d scalers -tAc "select count(*) from assets where campaign_id='portfolio:skindesign' and content->>'media'<>'video' and jsonb_array_length(coalesce(content->'motifs','[]'::jsonb)) > 0;"
Write-Host ("   artwork tagged: " + ($tagged.Trim()) + " pieces have motif tags  (expect > 0 — else posts get no image)")
# Meta verify (publish stays gated regardless)
try {
    $meta = (Invoke-WebRequest -Uri "http://localhost:$EnginePort/studio/meta/verify" -UseBasicParsing -TimeoutSec 8).Content | ConvertFrom-Json
    Write-Host ("   meta publish  : " + $meta.publishReady + "  (TEST MODE stays ON regardless — nothing sends)")
} catch { Write-Host "   meta verify   : (skipped — offline is fine, TEST MODE stays ON)" }

Write-Host ""
Write-Host "READY FOR THE MEETING:" -ForegroundColor Green
Write-Host "  Console  http://localhost:$ConsolePort" -ForegroundColor Green
Write-Host "  Engine   http://localhost:$EnginePort/healthz"
Write-Host "  Fleet    http://localhost:$EnginePort/studio/fleet"
Write-Host "  Ready q  http://localhost:$EnginePort/studio/social/ready"
Write-Host ""
Write-Host "Open the Console, hit '+ New session', and drive the sheet in WHAT-TO-ASK.md." -ForegroundColor Yellow
Write-Host "SAFE MODE is ON: nothing sends to real customers. Go-live is a separate flag flip." -ForegroundColor Yellow
