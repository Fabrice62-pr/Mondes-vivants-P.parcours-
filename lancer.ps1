# Lance tout le nécessaire pour GUICHET, puis ouvre Godot.
#   .\lancer.ps1            Kafka + bridge + Godot (le run se lance ensuite depuis Godot)
#   .\lancer.ps1 -SansGodot Kafka + bridge seulement
# Arrêt : .\arreter.ps1
param([switch]$SansGodot)

# Pas de "Stop" global : sous PowerShell 5.1, la moindre ligne écrite par docker sur stderr
# deviendrait une erreur fatale. Les échecs sont vérifiés explicitement ($LASTEXITCODE, throw).
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$logs = Join-Path $PSScriptRoot "data\logs"
New-Item -ItemType Directory -Force $logs | Out-Null

# Variables de .env (GODOT_EXE, KAFKA_BOOTSTRAP…)
$envVars = @{}
if (Test-Path ".env") {
    foreach ($ligne in Get-Content ".env" -Encoding UTF8) {
        if ($ligne -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { $envVars[$Matches[1]] = $Matches[2].Trim() }
    }
} else {
    Write-Host "Attention : pas de fichier .env (les runs avec LLM ne fonctionneront pas)." -ForegroundColor Yellow
}

# 1. Docker et Kafka
Write-Host "1/4 Kafka..." -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker ne répond pas : démarrez Docker Desktop puis relancez ce script." }
cmd /c "docker compose up -d > `"$logs\docker.log`" 2>&1"
if ($LASTEXITCODE -ne 0) { throw "docker compose a échoué (voir data\logs\docker.log)." }
$pret = $false
for ($i = 0; $i -lt 60; $i++) {
    $etat = docker inspect -f "{{.State.Health.Status}}" guichet-kafka 2>$null
    if ($etat -eq "healthy") { $pret = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $pret) { throw "Kafka n'est pas prêt après 2 minutes (voir data\logs\docker.log)." }
Write-Host "    Kafka prêt (interface : http://localhost:8080)"

# 2. Bridge Godot <-> Kafka (un seul à la fois : le port 8765 doit être libre)
Write-Host "2/4 Bridge..." -ForegroundColor Cyan
$occupe = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($occupe) {
    Write-Host "    Bridge déjà en marche (port 8765)."
} else {
    $env:PYTHONIOENCODING = "utf-8"
    $p = Start-Process -FilePath $py -ArgumentList "-m", "services.bridge" -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "bridge.log") -RedirectStandardError (Join-Path $logs "bridge.err.log")
    $p.Id | Out-File -Encoding ascii (Join-Path $logs "bridge.pid")
    Start-Sleep -Seconds 2
    if ($p.HasExited) { throw "Le bridge s'est arrêté (voir data\logs\bridge.err.log)." }
    Write-Host "    Bridge prêt sur ws://127.0.0.1:8765 (journal : data\logs\bridge.log)"
}

# 3. Service d'indicateurs (ICC recalculé depuis Kafka, retard mesuré : C7)
Write-Host "3/4 Service d'indicateurs..." -ForegroundColor Cyan
$deja = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*services.indicateurs*" }
if ($deja) {
    Write-Host "    Déjà en marche."
} else {
    $env:PYTHONIOENCODING = "utf-8"
    Start-Process -FilePath $py -ArgumentList "-m", "services.indicateurs" -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "indicateurs.log") `
        -RedirectStandardError (Join-Path $logs "indicateurs.err.log") | Out-Null
    Write-Host "    Service prêt (journal : data\logs\indicateurs.log)"
}

# 4. Godot
if ($SansGodot) { Write-Host "Prêt. Ouvrez le projet guichet\ dans Godot."; exit 0 }
Write-Host "4/4 Godot..." -ForegroundColor Cyan
$godot = $envVars["GODOT_EXE"]
if (-not $godot) { $godot = "C:\Users\LEGION\Desktop\EIGSI 5A\Godot_v4.7.2-stable_win64.exe\Godot_v4.7.2-stable_win64.exe" }
if (-not (Test-Path $godot)) {
    Write-Host "    Godot introuvable ($godot). Ouvrez le projet guichet\ à la main, ou renseignez GODOT_EXE dans .env." -ForegroundColor Yellow
    exit 0
}
# Guillemets explicites : sous PowerShell 5.1, Start-Process ne protège pas les espaces des arguments
# (« EIGSI 5A » couperait le chemin en deux et Godot ne trouverait pas le projet).
$projet = Join-Path $PSScriptRoot "guichet"
$p = Start-Process -FilePath $godot -ArgumentList "--path", "`"$projet`"" -PassThru
Start-Sleep -Seconds 3
if ($p.HasExited) {
    Write-Host "    Godot s'est fermé aussitôt (code $($p.ExitCode)). Ouvrez le projet guichet\ à la main." -ForegroundColor Yellow
    exit 1
}
Write-Host "Prêt. Dans Godot : choisissez une configuration puis cliquez sur « Lancer »." -ForegroundColor Green
