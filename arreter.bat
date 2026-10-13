@echo off
rem Double-clic : arrête le bridge et le moteur (ajoutez -Kafka pour arrêter aussi Kafka).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0arreter.ps1" %*
