@echo off
REM ============================================================
REM  Mail Lobster - Build the GUI launcher as a Windows EXE
REM
REM  Notes:
REM    - This script uses ASCII only to stay compatible with
REM      Windows PowerShell (default code page GBK on Chinese
REM      Windows). Non-ASCII characters break batch parsing
REM      under PowerShell.
REM    - Set PIP_INDEX_URL env var to override the default
REM      Tsinghua mirror (e.g. https://pypi.org/simple).
REM    - Spec excludes in launcher.spec trim torch/cv2/scipy
REM      transitive deps and reduce build time + EXE size.
REM ============================================================

setlocal
chcp 65001 >nul 2>&1

cd /d "%~dp0"

if "%PIP_INDEX_URL%"=="" set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

echo.
echo  [1/5] Checking Python...
python -c "import sys; print('Python', sys.version.split()[0])"
if errorlevel 1 goto :fail

echo.
echo  [2/5] Installing PySide6 and PyInstaller (dev only)...
echo        mirror: %PIP_INDEX_URL%
python -m pip install --upgrade -i "%PIP_INDEX_URL%" pyinstaller PySide6
if errorlevel 1 goto :fail

echo.
echo  [3/5] Generating launcher icon assets/lobster.ico...
if not exist "assets" mkdir assets
python gui/icon.py assets/lobster.ico
if errorlevel 1 goto :fail

echo.
echo  [4/5] Clearing PyInstaller build cache...
if exist build rmdir /s /q build

echo.
echo  [5/5] Running PyInstaller (with spec excludes)...
python -m PyInstaller launcher.spec --noconfirm --clean --log-level WARN
if errorlevel 1 goto :fail

echo.
echo  ============================================
echo   Build complete: dist\mail_lobster\mail_lobster.exe
echo  ============================================
echo.
exit /b 0

:fail
echo.
echo  *** Build failed ***
exit /b 1
