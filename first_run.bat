@echo off
chcp 936 >nul 2>&1
title Mail Lobster v2 - 邮件管理系统
cd /d "%~dp0"

echo.
echo  ============================================
echo    Mail Lobster v2 - 邮件管理系统
echo  ============================================
echo.

:: ============================================
::  启动时自动备份配置
:: ============================================
python -X utf8 _backup_config.py backup >nul 2>&1

:: ============================================
::  检查 Python 环境
:: ============================================
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [错误] 未找到 Python，请先安装 Python 3.10+
    echo  下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: ============================================
::  安装依赖库
:: ============================================
python -c "import requests, openpyxl, openai" >nul 2>&1
if %errorlevel% neq 0 (
    echo  [提示] 首次运行，正在安装依赖...
    pip install -r requirements.txt -q
    if %errorlevel% neq 0 (
        echo  [错误] 依赖安装失败，请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo  [完成] 依赖安装成功
    echo.
)

:: ============================================
::  检查配置文件
:: ============================================
if not exist "config_imap_forward.json" (
    echo  [!] 配置文件不存在，需要首次配置
    echo.
    goto :run_wizard
)

python -c "import json,sys; c=json.load(open('config_imap_forward.json','r',encoding='utf-8')); email=c.get('imap',{}).get('email',''); sys.exit(0 if 'your_email' in email or email=='' else 1)" 2>nul
if %errorlevel% == 0 (
    echo  [!] 配置文件中邮箱信息未填写，需要进行配置
    echo.
    goto :run_wizard
)

:: ============================================
::  显示配置摘要
:: ============================================
python -X utf8 _display_config.py 2>nul
echo.

:: ============================================
::  主菜单
:: ============================================
echo  --------------------------------------------
echo  请选择操作:
echo.
echo     1. 单次检查 (拉取邮件测试)
echo     2. 守护进程模式 (持续监控)
echo     3. 手动生成总结
echo     4. 测试飞书通知
echo     5. 重新配置向导
echo     6. 双平台映射配置 (AMZ/WMT分流)
echo     7. 配置撤回修改 (恢复上次配置)
echo     8. 查看备份列表
echo     9. 分级报错 (报告分类错误)
echo    10. 退出
echo  --------------------------------------------
echo.
set /p choice="  请选择 (1-10): "

if "%choice%"=="1" goto :run_once
if "%choice%"=="2" goto :run_daemon
if "%choice%"=="3" goto :run_summary
if "%choice%"=="4" goto :test_feishu
if "%choice%"=="5" goto :run_wizard
if "%choice%"=="6" goto :config_sister
if "%choice%"=="7" goto :restore_config
if "%choice%"=="8" goto :list_backups
if "%choice%"=="9" goto :report_error
if "%choice%"=="10" exit /b

echo.
echo  [!] 无效选择，默认执行单次检查...
echo.
goto :run_once

:: ============================================
::  单次检查
:: ============================================
:run_once
echo.
echo  > 执行单次邮件检查...
echo.
python -X utf8 mail_lobster_imap.py --once
echo.
echo  [完成] 单次检查完成
pause
exit /b

:: ============================================
::  守护进程
:: ============================================
:run_daemon
echo.
echo  > 启动守护进程 (Ctrl+C 退出)
echo.
python -X utf8 mail_lobster_imap.py
echo.
echo  [停止] 守护进程已退出
pause
exit /b

:: ============================================
::  手动总结
:: ============================================
:run_summary
echo.
echo  ============================================
echo    Mail Lobster v2 - 手动总结
echo  ============================================
echo.
echo     1. 07:30 早间总结
echo     2. 11:30 午间总结
echo     3. 17:00 傍晚总结
echo     4. 21:00 夜间总结 (含全平台总结)
echo     5. 自定义 (选择时段)
echo.
set /p summary_choice="  请选择 (1-5): "

if "%summary_choice%"=="1" set summary_time=07:30
if "%summary_choice%"=="2" set summary_time=11:30
if "%summary_choice%"=="3" set summary_time=17:00
if "%summary_choice%"=="4" set summary_time=21:00
if "%summary_choice%"=="5" set summary_time=
if "%summary_choice%"=="" goto :run_summary

echo.
if "%summary_time%"=="" (
    echo  > 自动选择时段总结...
) else (
    echo  > 生成 %summary_time% 总结...
)
echo.
if "%summary_time%"=="" (
    python -X utf8 mail_lobster_imap.py --summary
) else (
    python -X utf8 mail_lobster_imap.py --summary %summary_time%
)
echo.
pause
exit /b

:: ============================================
::  测试飞书
:: ============================================
:test_feishu
echo.
echo  ============================================
echo    飞书通知测试
echo  ============================================
echo.
echo     1. 测试群聊机器人
echo     2. 测试私聊应用私聊
echo     3. 返回主菜单
echo.
set /p feishu_choice="  请选择 (1-3): "

if "%feishu_choice%"=="1" (
    echo.
    echo  > 发送群聊测试消息...
    python -X utf8 mail_lobster_imap.py --test-feishu
)
if "%feishu_choice%"=="2" (
    echo.
    echo  > 发送私聊测试消息...
    python -X utf8 mail_lobster_imap.py --test-feishu-app
)
if "%feishu_choice%"=="3" goto :eof
if "%feishu_choice%"=="" goto :eof
echo.
pause
exit /b

:: ============================================
::  双平台映射配置
:: ============================================
:config_sister
echo.
echo  ============================================
echo    双平台映射配置 (AMZ/WMT 分流)
echo  ============================================
echo.
echo  配置双平台企业的 AMZ/WMT 负责人映射关系。
echo  当同一邮件涉及不同平台需要转发时，
echo  系统通过原始发件人判断平台并自动
echo  路由到对应平台的负责人。
echo.
python -X utf8 _config_sister.py
echo.
pause
exit /b

:: ============================================
::  配置撤回
:: ============================================
:restore_config
echo.
echo  ============================================
echo    配置撤回 - 恢复备份
echo  ============================================
echo.
echo  可以恢复之前的配置备份版本。
echo  恢复前会自动备份当前配置，防止意外。
echo.
python -X utf8 _backup_config.py list
echo.
echo  --------------------------------------------
echo     1. 恢复最新一次备份
echo     2. 恢复指定编号 (交互式)
echo     3. 返回主菜单
echo  --------------------------------------------
echo.
set /p restore_choice="  请选择 (1-3): "

if "%restore_choice%"=="1" (
    echo.
    echo  > 恢复最新一次备份...
    python -X utf8 _backup_config.py restore
)
if "%restore_choice%"=="2" (
    echo.
    set /p restore_num="  请输入备份编号: "
    python -X utf8 _backup_config.py restore %restore_num%
)
if "%restore_choice%"=="3" goto :eof
if "%restore_choice%"=="" goto :eof
echo.
pause
exit /b

:: ============================================
::  查看备份列表
:: ============================================
:list_backups
echo.
echo  ============================================
echo    配置备份列表
echo  ============================================
echo.
python -X utf8 _backup_config.py list
echo.
pause
exit /b

:: ============================================
::  分级报错
:: ============================================
:report_error
echo.
echo  ============================================
echo    分级报错 - 报告分类错误
echo  ============================================
echo.
echo  如果飞书推送的邮件分级不正确，
echo  可以在这里报告。系统会在下次总结后
echo  自动分析错误并优化分类提示词。
echo.
python -X utf8 _report_error.py
echo.
pause
exit /b

:: ============================================
::  配置向导
:: ============================================
:run_wizard
echo.
echo  ============================================
echo    配置向导
echo  ============================================
echo.
echo  向导将引导您完成以下配置:
echo.
echo     - IMAP 邮箱设置 (邮件转发汇总)
echo     - 负责人映射 (业务邮箱 -> 负责人)
echo     - AI 分析 (DeepSeek 智能分析)
echo     - 飞书群聊机器人 (群知通)
echo     - 飞书自建应用 (私聊通知)
echo     - 其他平台路由 (OZON/Temu 等)
echo     - 双平台分流 (睿宝/诚俊 AMZ+WMT)
echo.
python -X utf8 setup_wizard.py

echo.
echo  ============================================
echo    配置完成!
echo  ============================================
echo.
echo  定时总结: 07:30 / 11:30 / 17:00 / 21:00
echo  21:00 总结: 全平台全owner汇总发群聊
echo  周总结: 周六 17:00 (与当日傍晚总结同时触发)
echo.
echo  常用命令:
echo     first_run.bat    管理面板
echo     run_daemon.bat   守护进程
echo     run_once.bat     单次检查
echo     run_summary.bat  手动总结
echo.
pause
