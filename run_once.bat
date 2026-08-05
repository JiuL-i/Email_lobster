@echo off
title Mail Lobster v2 - 单次检查
cd /d "%~dp0"

echo.
echo  ============================================
echo    Mail Lobster v2 - 单次检查
echo  ============================================
echo.
echo  > 执行单次邮件检查...
echo.
python -X utf8 mail_lobster_imap.py --once
echo.
echo  [完成] 单次检查结束
pause

