@echo off
title Mail Lobster v2 - 飞书推送测试
cd /d "%~dp0"

echo.
echo  ============================================
echo    Mail Lobster v2 - 飞书推送测试
echo  ============================================
echo.
echo     1. 测试群聊机器人
echo     2. 测试自建应用私聊
echo.
set /p choice="  请选择 (1-2): "

echo.
if "%choice%"=="1" (
    echo  > 发送群聊测试消息...
    python -X utf8 mail_lobster_imap.py --test-feishu
) else if "%choice%"=="2" (
    echo  > 发送私聊测试消息...
    python -X utf8 mail_lobster_imap.py --test-feishu-app
) else (
    echo  [!] 无效选择
)
echo.
pause

