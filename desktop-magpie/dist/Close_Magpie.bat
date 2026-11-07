@echo off
rem Sends a "go home" signal to every running Desktop Magpie.
rem Works even if the magpie runs as administrator or the exe was renamed.
cd /d "%~dp0"
if exist DesktopMagpie.exe (
  DesktopMagpie.exe --quit
) else (
  for %%f in (*.exe) do "%%f" --quit
)
