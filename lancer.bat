@echo off
rem Double-clic : lance Kafka, le bridge et Godot (voir lancer.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lancer.ps1" %*
if errorlevel 1 pause
