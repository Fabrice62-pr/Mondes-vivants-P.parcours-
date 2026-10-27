# Arrête le bridge, le service d'indicateurs et le moteur ou le rejeu éventuellement en cours.
#   .\arreter.ps1           arrête tout sauf Docker (Kafka, PostgreSQL)
#   .\arreter.ps1 -Kafka    arrête aussi Kafka et PostgreSQL (docker compose stop)
param([switch]$Kafka)

Set-Location $PSScriptRoot
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*services.bridge*" -or $_.CommandLine -like "*engine.run*" -or
                   $_.CommandLine -like "*services.indicateurs*" -or $_.CommandLine -like "*services.rejeu*" }
foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Arrêté : $($p.CommandLine -replace '.*-m ', '')"
}
Remove-Item -ErrorAction SilentlyContinue (Join-Path $PSScriptRoot "data\logs\bridge.pid")
if ($Kafka) {
    docker compose stop
    Write-Host "Kafka et PostgreSQL arrêtés (les souvenirs restent dans le volume guichet-pg)."
}
