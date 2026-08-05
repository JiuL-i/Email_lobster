# Mail Lobster v2 - 邮件龙虾

跨境电商邮件智能管理系统 — IMAP 监控 + AI 分析 + 飞书通知 + 定时总结

---

## 核心功能

| 功能 | 说明 |
|------|------|
| **全天监控** | 守护进程模式，每 5 分钟自动拉取邮件 |
| **AI 智能分析** | DeepSeek 分析邮件内容，7 级分类（0=紧急 → 6=无关） |
| **飞书即时推送** | 0/1/2 级邮件立即通知：群聊机器人 + 私聊应用 |
| **双平台分流** | 同一企业的 AMZ/WMT 邮箱自动识别平台，精准路由到对应负责人 |
| **物流识别** | 物流/服务商邮件保留原负责人，不误判为"其他平台" |
| **同轮去重** | 相同邮件从多邮箱转发时只推送一次 |
| **定时总结** | 每天 4 次（07:30 / 11:30 / 17:00 / 21:00）+ 21:00 全平台日总结 |
| **周总结** | 每周六 17:00 自动生成周统计报表 |
| **配置备份** | 自动备份配置，支持一键撤回修改 |

---

## 系统架构

```
Outlook 业务邮箱 (N 个)
  店铺A_AMZ / 店铺B_WMT / 店铺C_AMZ / ...
       ↓ 各自设置自动转发
汇总邮箱 (your_email@example.com)
       ↓ IMAP/SSL 拉取
Mail Lobster v2 (守护进程)
       ↓
  ┌─ 识别来源邮箱 → 匹配负责人 (owner)
  ├─ AI 分类 → 级别 + 平台 + 摘要
  ├─ 发件人平台检测 → 校正 AI 误判
  ├─ 双平台分流 → 店铺A_AMZ/WMT 精准路由
  ├─ 物流检测 → 云途/DHL等保留原 owner
  ├─ 同轮去重 → 相同邮件只保留一份
  ├─ 其他平台验证 → OZON/Temu 等转给指定 handler
  └─ 即时通知 → 飞书群聊/私聊/both
       ↓
  定时总结 → 飞书推送 + Excel 报表
```

---

## 通知规则

### 即时通知（0/1/2 级邮件到达时）

| 渠道 | 通知方式 |
|------|---------|
| `private` | 飞书私聊 → 对应手机号 |
| `group` | 群聊机器人 |
| `both` | 群聊机器人 + 飞书私聊（所有手机号） |
| 其他平台 | 飞书私聊 → handler（指定负责人） |

### 分时段总结（每天 4 次）

| 时段 | 行为 |
|------|------|
| 07:30 / 11:30 | 各 owner 按渠道收到自己的邮件总结 |
| 17:00 | 各 owner 按渠道收到总结 + **群聊收到全平台简化简报**（仅 0/1/2 级数量，按店铺逐行） |
| 21:00 | 各 owner 收到总结 + **群聊收到全平台全天汇总** |

### 周总结（周六 17:00）

在日常总结基础上，额外生成按平台+负责人分组的周统计报表。

---

## 双平台分流

部分企业同时运营 Amazon 和 Walmart 店铺，使用两个独立邮箱。系统通过分析原始发件人来判断邮件属于哪个平台，自动路由到对应负责人。

```json
// config_imap_forward.json
"sister_owner_mapping": [
    {
        "business": "企业A",
        "owners": { "AMZ": "店铺A_AMZ", "WMS": "店铺A_WMT" }
    },
    {
        "business": "企业B",
        "owners": { "AMZ": "店铺B_AMZ", "WMS": "店铺B_WMT" }
    }
]
```

**分流示例：**
- 店铺A AMZ 邮箱收到 Amazon 邮件 → 保持「店铺A_AMZ」
- 店铺A AMZ 邮箱收到 Walmart 邮件 → 转给「店铺A_WMT」
- 店铺A WMT 邮箱收到 Amazon 邮件 → 转给「店铺A_AMZ」

---

## 物流/服务商识别

系统自动识别物流/服务商邮件（云途、DHL、顺丰、4PX 等），这类邮件**不会**被路由到"其他平台" handler，而是保留给原始负责人。

判断逻辑：
- 发件人域名匹配物流域名库 → 物流邮件
- 正文含物流关键词（运单、tracking、shipment 等）且无电商关键词 → 物流邮件
- 若正文同时提到 FBA/WFS/listing 等 → 识别为电商平台邮件

---

## 同轮去重

同一封邮件从多个邮箱转发到汇总邮箱时，系统通过内容指纹（subject + from + preview）去重，同一轮检查中只保留一份，只推送一次。

---

## 快速开始

### 1. 双击 first_run.bat

启动面板会自动：
- 检查 Python 环境
- 安装依赖
- 显示配置摘要
- 自动备份当前配置

### 2. 菜单选项

| 选项 | 说明 |
|------|------|
| 1. 单次检查 | 测试拉取邮件 |
| 2. 守护进程 | 持续监控（按 Ctrl+C 退出） |
| 3. 手动总结 | 选择时段生成总结 |
| 4. 测试飞书 | 测试群聊/私聊推送 |
| 5. 设置向导 | 重新配置系统 |
| 6. 撤回修改 | 恢复到上次配置备份 |
| 7. 查看备份 | 列出所有配置备份 |
| 8. 退出 | |

### 3. 命令行

```bash
python mail_lobster_imap.py                  # 守护进程
python mail_lobster_imap.py --once           # 单次检查
python mail_lobster_imap.py --summary 07:30  # 手动总结（指定时段）
python mail_lobster_imap.py --summary        # 手动总结（自动选择时段）
python mail_lobster_imap.py --test-feishu    # 测试群聊推送
python mail_lobster_imap.py --test-feishu-app # 测试私聊推送
```

---

## 配置文件

`config_imap_forward.json` 主要字段：

```json
{
  "imap": { "host": "imap.139.com", "port": 993, "email": "...", "password": "..." },
  "mailbox_mapping": {
    "mailboxes": [
      { "source_email": "xxx@outlook.com", "owner": "负责人AMZ", "notify_channel": "private" }
    ]
  },
  "ai": { "enabled": true, "provider": "deepseek", ... },
  "feishu_bot": { "enabled": true, "webhook_url": "...", "notify_levels": [0,1,2] },
  "feishu_app": { "enabled": true, "owner_mapping": {"负责人": "+86xxx"}, ... },
  "other_platform": { "enabled": true, "owner": "Store_Default" },
  "sister_owner_mapping": [
    { "business": "企业A", "owners": { "AMZ": "店铺A_AMZ", "WMS": "店铺A_WMT" } }
  ],
  "schedule": { "summaries": ["07:30", "11:30", "17:00", "21:00"] },
  "heartbeat": { "enabled": true, "path": "state/heartbeat" }
}
```

### 渠道类型

| `notify_channel` | 说明 |
|------------------|------|
| `private` | 飞书私聊（默认） |
| `group` | 群聊机器人 |
| `both` | 群聊 + 私聊同时发送 |

### 多手机号

`owner_mapping` 支持数组格式，一个 owner 对应多个手机号：

```json
"owner_mapping": {
    "负责人": "+8613800001111",
    "双手机负责人": ["+8613800001111", "+8613900002222"]
}
```

---

## AI 分级标准

| 级别 | 含义 | 子类别示例 |
|------|------|-----------|
| 0 | 紧急 — 店铺级问题 | 店铺关停、POA 申诉、重大 Case |
| 1 | 重要 — 商品/账户安全 | 系统质检、商品下架、认证审核、账户安全 |
| 2 | 关注 — 业务调整 | 系统调整、资金付款、货件到仓、退款退货 |
| 3 | 一般 — 客服往来 | 客服来信、投诉咨询 |
| 4 | 日常 — 运营管理 | 管理报告、运营数据 |
| 5 | 低优 — 出单发货 | 新订单、发货通知 |
| 6 | 无关 — 营销推广 | 活动推荐、广告推销、培训邀请 |

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `mail_lobster_imap.py` | 主程序（IMAP + AI + 飞书 + 总结） |
| `ai_analyzer.py` | AI 分析模块（DeepSeek） |
| `config_imap_forward.json` | 配置文件 |
| `first_run.bat` | 启动面板（主入口） |
| `run_daemon.bat` | 守护进程 |
| `run_once.bat` | 单次检查 |
| `run_summary.bat` | 手动总结 |
| `测试飞书.bat` | 飞书推送测试 |
| `_display_config.py` | 配置摘要显示 |
| `_backup_config.py` | 配置备份/恢复 |
| `setup_wizard.py` | 设置向导 |
| `requirements.txt` | Python 依赖 |
| `backup/` | 配置备份目录（自动） |
| `reports/cycles/` | 周期报表（自动） |
| `reports/summaries/` | 总结报表（自动） |
| `state/` | 心跳/状态文件（自动） |

---

## 心跳机制

守护进程每 60 秒写入时间戳到 `state/heartbeat` 文件。WSL 端 OpenClaw 看门狗可通过检查文件修改时间判断进程是否存活。

---

## 常见问题

**Q: 如何添加新的双平台分流？**
在 config 的 `sister_owner_mapping` 中添加新条目：
```json
{ "business": "企业名", "owners": { "AMZ": "XX_AMZ", "WMS": "XX_WMT" } }
```

**Q: 如何撤回配置修改？**
first_run.bat 菜单选 6（撤回修改），自动恢复到最近一次备份。恢复前会自动备份当前配置。

**Q: 物流邮件被误判为其他平台？**
系统会自动识别物流域名和关键词。如有遗漏，可在 `_is_logistics_email()` 方法中添加新的关键词。

**Q: 同一个 owner 如何配置多个手机号？**
`owner_mapping` 中改为数组格式：`"负责人": ["+86xxx", "+86yyy"]`

**Q: 如何同时发群聊和私聊？**
将 `notify_channel` 设为 `"both"`。

---

**Mail Lobster v2** — 智能邮件管理，跨境电商无忧。
