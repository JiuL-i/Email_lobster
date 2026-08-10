@echo off
REM ============================================================
REM  Mail Lobster - Build the GUI launcher as a Windows EXE
REM ============================================================
setlocal

cd /d "%~dp0"

echo.
echo  [1/4] 检查 Python 与依赖...
python -c "import sys; print('Python', sys.version.split()[0])"

echo.
echo  [2/4] 安装 PySide6 与 PyInstaller (仅开发环境需要)
python -m pip install --upgrade pyinstaller PySide6
if errorlevel 1 goto :fail

echo.
echo  [3/4] 生成启动器图标 assets/lobster.ico
python gui/icon.py assets/lobster.ico
if errorlevel 1 goto :fail

echo.
echo  [4/4] 运行 PyInstaller...
pyinstaller launcher.spec --noconfirm --clean
if errorlevel 1 goto :fail

echo.
echo  ============================================
echo   打包完成：dist\mail_lobster\mail_lobster.exe
echo  ============================================
echo.
exit /b 0

:fail
echo.
echo  *** 打包失败 ***
exit /b 1