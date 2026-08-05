@echo off
title Mail Lobster v2 - 手动总结
cd /d "%~dp0"

echo.
echo  ============================================
echo    Mail Lobster v2 - 手动总结
echo  ============================================
echo.
echo     1. 07:30 早间总结
echo     2. 11:30 午间总结
echo     3. 17:00 傍晚总结 (含全平台日总结)
echo     4. 21:00 夜间总结 (含全平台日总结)
echo     5. 自动 (选择最近时段)
echo.
set /p choice="  请选择 (1-5): "

echo.
if "%choice%"=="1" (
    echo  > 生成 07:30 早间总结...
    python -X utf8 mail_lobster_imap.py --summary 07:30
) else if "%choice%"=="2" (
    echo  > 生成 11:30 午间总结...
    python -X utf8 mail_lobster_imap.py --summary 11:30
) else if "%choice%"=="3" (
    echo  > 生成 17:00 傍晚总结...
    python -X utf8 mail_lobster_imap.py --summary 17:00
) else if "%choice%"=="4" (
    echo  > 生成 21:00 夜间总结...
    python -X utf8 mail_lobster_imap.py --summary 21:00
) else (
    echo  > 自动生成最近时段总结...
    python -X utf8 mail_lobster_imap.py --summary
)
echo.
pause

