@echo off
title Mail Lobster v2 - GUI Launcher
cd /d "%~dp0"

REM ============================================================
REM 优先启动打包好的 GUI 启动器 (mail_lobster.exe)；
REM 未打包时退回到原始的 python 守护进程模式，便于开发调试。
REM ============================================================

set EXE_PATH=dist\mail_lobster\mail_lobster.exe

if exist "%EXE_PATH%" (
    echo 启动 GUI 监控面板: %EXE_PATH%
    "%EXE_PATH%"
    goto :end
)

echo.
echo  ============================================
echo    Mail Lobster v2 - 守护进程模式（开发回退）
echo  ============================================
echo.
echo   检查间隔:   每 5 分钟
echo   定时总结:   07:30 / 11:30 / 17:00 / 21:00
echo   周  总  结:  周六 17:00
echo.
echo   按 Ctrl+C 退出
echo  --------------------------------------------
echo.
python -X utf8 mail_lobster_imap.py
echo.
echo  [已停止] 守护进程已退出
pause

:end
endlocal