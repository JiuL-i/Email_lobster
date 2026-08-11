@echo off
REM ============================================================
REM  Mail Lobster - Build the GUI launcher as a Windows EXE
REM ============================================================

setlocal
chcp 65001 >nul 2>&1

cd /d "%~dp0"

echo.
echo  [1/4] Checking Python...
python -c "import sys; print('Python', sys.version.split()[0])"
if errorlevel 1 goto :fail

echo.
echo  [2/4] Installing PySide6 and PyInstaller (dev only)...
python -m pip install --upgrade pyinstaller PySide6
if errorlevel 1 goto :fail

echo.
echo  [3/4] Generating launcher icon assets/lobster.ico...
if not exist "assets" mkdir assets
python gui/icon.py assets/lobster.ico
if errorlevel 1 goto :fail

echo.
echo  [4/4] Running PyInstaller...
pyinstaller launcher.spec --noconfirm --clean
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