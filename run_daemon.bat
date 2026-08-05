@echo off
title Mail Lobster v2 - 守护进程
cd /d "%~dp0"

echo.
echo  ============================================
echo    Mail Lobster v2 - 守护进程模式
echo  ============================================
echo.
echo   检查间隔:  每 5 分钟
echo   定时总结:  07:30 / 11:30 / 17:00 / 21:00
echo   周总结:    周六 17:00
echo.
echo   按 Ctrl+C 退出
echo  --------------------------------------------
echo.
python -X utf8 mail_lobster_imap.py
echo.
echo  [已停止] 守护进程已退出
pause

