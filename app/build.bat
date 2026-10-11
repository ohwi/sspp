@echo off
setlocal
cd /d "%~dp0"
set "PP_PYTHON=.venv\Scripts\python.exe"
if not exist "%PP_PYTHON%" set "PP_PYTHON=..\.venv\Scripts\python.exe"
if not exist "%PP_PYTHON%" (
  echo Create .venv and install requirements-build.txt as described in README.md.
  exit /b 1
)
"%PP_PYTHON%" -m PyInstaller --noconfirm --clean --noconsole --onedir --name PotentialPlanner --distpath "..\PotentialPlanner_Build\dist" --workpath "..\PotentialPlanner_Build\build" --specpath "..\PotentialPlanner_Build" --paths "src" run.py
if errorlevel 1 exit /b 1
set "PP_DEST=..\PotentialPlanner_Build\dist\PotentialPlanner\resources"
for %%d in (presets templates\icons templates\potentials) do (
  xcopy "resources\%%d" "%PP_DEST%\%%d\" /E /I /Y /Q
  if errorlevel 1 exit /b 1
)
copy /Y resources\characters.json "%PP_DEST%\characters.json" >nul
if errorlevel 1 exit /b 1
copy /Y resources\template_provenance.json "%PP_DEST%\template_provenance.json" >nul
if errorlevel 1 exit /b 1
copy /Y resources\templates\potential-manifest.json "%PP_DEST%\templates\potential-manifest.json" >nul
if errorlevel 1 exit /b 1
copy /Y resources\templates\faces-manifest.json "%PP_DEST%\templates\faces-manifest.json" >nul
if errorlevel 1 exit /b 1
echo Build saved to ..\PotentialPlanner_Build\dist\PotentialPlanner
