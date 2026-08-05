# -*- coding: utf-8 -*-
"""提示词优化引擎 — 根据误分类报告自动补强 AI 提示词
可由守护进程在总结后自动调用，也可手动运行。

用法:
  python _prompt_optimizer.py              手动触发优化
  python _prompt_optimizer.py --dry-run    只分析不保存
  python _prompt_optimizer.py --status     查看当前状态
  python _prompt_optimizer.py --rollback   回滚到上一版本
"""
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE = 'config_imap_forward.json'
STATE_DIR = Path('state')
ERROR_FILE = STATE_DIR / 'misclassifications.jsonl'
ERROR_REPORT = STATE_DIR / 'error_report.md'
PROMPTS_DIR = Path('prompts')
RULES_FILE = PROMPTS_DIR / 'learned_rules.txt'
VERSIONS_DIR = PROMPTS_DIR / 'versions'
BASE_PROMPT_FILE = PROMPTS_DIR / 'classification_prompt_v1.txt'

MAX_RULES = 20
MAX_VERSIONS = 10


class PromptOptimizer:
    """提示词优化引擎"""

    def __init__(self, config: Dict = None):
        if config is None:
            config = self._load_config()
        self.config = config

        # 优化器 API 配置（默认复用 ai 段）
        opt_cfg = config.get('prompt_optimizer', {})
        ai_cfg = config.get('ai', {})
        self.enabled = opt_cfg.get('enabled', True)
        self.auto_optimize = opt_cfg.get('auto_optimize', True)
        self.max_rules = opt_cfg.get('max_rules', MAX_RULES)
        self.max_versions = opt_cfg.get('max_versions', MAX_VERSIONS)

        # API 设置（优先用 prompt_optimizer 段，否则复用 ai 段）
        self.api_base_url = opt_cfg.get('base_url', ai_cfg.get('base_url', 'https://api.deepseek.com/v1'))
        self.api_key = opt_cfg.get('api_key', ai_cfg.get('api_key', ''))
        self.model = opt_cfg.get('model', ai_cfg.get('model', 'deepseek-chat'))

    @staticmethod
    def _load_config() -> Dict:
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    # ================================================================
    #  主流程
    # ================================================================

    def optimize(self, dry_run: bool = False) -> bool:
        """主流程：收集错误 → AI 分析 → 生成新规则 → 保存"""
        if not self.enabled:
            logger.info("⏭️ 提示词优化已禁用")
            return False

        errors = self._load_unprocessed_errors()
        if not errors:
            logger.info("📭 没有新的误分类报告，跳过优化")
            return False

        logger.info(f"🔧 开始优化提示词（{len(errors)} 条误分类报告）...")

        current_rules = self._load_current_rules()
        current_prompt = self._load_current_prompt()

        # 调用 AI 分析
        new_rules = self._call_optimizer_ai(errors, current_prompt, current_rules)
        if not new_rules:
            logger.warning("⚠️ 优化 AI 未返回有效结果")
            return False

        if dry_run:
            logger.info("🔍 [DRY RUN] 生成的新规则:")
            print(new_rules)
            return True

        # 备份旧规则
        self._backup_current_rules()

        # 写入新规则
        self._save_new_rules(new_rules)

        # 标记已处理
        self._mark_processed(errors)

        # 生成人类可读报告
        self._generate_error_report(errors, current_rules, new_rules)

        logger.info(f"✅ 提示词优化完成（处理了 {len(errors)} 条报告）")
        return True

    # ================================================================
    #  数据加载
    # ================================================================

    def _load_unprocessed_errors(self) -> List[Dict]:
        """加载未处理的错误报告"""
        if not ERROR_FILE.exists():
            return []
        errors = []
        with open(ERROR_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                        if not e.get('processed', False):
                            errors.append(e)
                    except json.JSONDecodeError:
                        continue
        return errors

    def _load_current_rules(self) -> str:
        """加载当前补充规则"""
        if RULES_FILE.exists():
            return RULES_FILE.read_text(encoding='utf-8')
        return ''

    def _load_current_prompt(self) -> str:
        """加载基础提示词"""
        if BASE_PROMPT_FILE.exists():
            return BASE_PROMPT_FILE.read_text(encoding='utf-8')
        return '(基础提示词文件不存在)'

    # ================================================================
    #  AI 调用
    # ================================================================

    def _call_optimizer_ai(self, errors: List[Dict], current_prompt: str, current_rules: str) -> Optional[str]:
        """调用 AI 分析错误并生成补强规则"""
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("⚠️ openai 库未安装，无法进行提示词优化")
            return None

        if not self.api_key or self.api_key.startswith('你的'):
            logger.warning("⚠️ API Key 未配置")
            return None

        try:
            client = OpenAI(api_key=self.api_key, base_url=self.api_base_url)
        except Exception as e:
            logger.warning(f"⚠️ 创建 AI 客户端失败: {e}")
            return None

        # 构建错误报告文本
        error_text = ''
        for i, e in enumerate(errors, 1):
            error_text += f"""
### 误分类 {i}
- 邮件主题: {e.get('email_subject', '未知')}
- 发件人: {e.get('email_sender', '未知')}
- 负责人: {e.get('email_owner', '未知')}
- 日期: {e.get('email_date', '未知')}
- 错误分级: Level {e.get('wrong_level', '?')} ({e.get('wrong_sub_category', '未知')})
- 正确分级: Level {e.get('correct_level', '?')} ({e.get('correct_sub_category', '未知')})
- 用户备注: {e.get('reporter_note', '无')}
"""

        now = datetime.now().strftime('%Y-%m-%d %H:%M')

        optimizer_prompt = f"""你是一个跨境电商邮件分类系统的提示词优化专家。

系统使用以下分类提示词（核心部分）：
---
{current_prompt[:3000]}
---

当前已学习的补充规则：
---
{current_rules if current_rules else '(暂无)'}
---

以下是最近的误分类报告（共 {len(errors)} 条）：
{error_text}

请分析这些误分类的共同模式和根因，并在补充规则中添加针对性规则来防止类似错误。
要求：
1. 每条规则要具体，直接描述容易混淆的场景和正确判断方式
2. 不要重复已有规则中已经覆盖的内容
3. 保持规则简洁（每条不超过 80 字）
4. 总规则数不超过 {self.max_rules} 条（超过则合并或删除最旧的）
5. 只输出规则内容，不要输出分析过程

严格按以下格式输出：
```
# === AI 自动学习的补充规则 ===
# 最后更新: {now}
# 版本: v{{VERSION}}

1. 具体规则描述...
2. 具体规则描述...
```

其中 {{VERSION}} 替换为递增的版本号（当前版本从注释中提取，加 1）。"""

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是AI提示词优化专家。只返回优化后的补充规则文本，不要返回分析过程。严格按指定格式输出。"},
                    {"role": "user", "content": optimizer_prompt}
                ],
                temperature=0.2,
                max_tokens=2000,
                timeout=60
            )

            content = response.choices[0].message.content.strip()

            # 清理 markdown 代码块标记
            if content.startswith('```'):
                lines = content.split('\n')
                # 去掉首尾的 ``` 行
                if lines[0].startswith('```'):
                    lines = lines[1:]
                if lines and lines[-1].strip() == '```':
                    lines = lines[:-1]
                content = '\n'.join(lines)

            # 确保有规则头
            if not content.startswith('#'):
                content = f"# === AI 自动学习的补充规则 ===\n# 最后更新: {now}\n\n{content}"

            return content

        except Exception as e:
            logger.warning(f"⚠️ 优化 AI 调用失败: {e}")
            return None

    # ================================================================
    #  文件操作
    # ================================================================

    def _backup_current_rules(self):
        """备份当前规则到 versions/"""
        if not RULES_FILE.exists():
            return

        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

        # 解析当前版本号
        version = 'v0'
        rules_text = RULES_FILE.read_text(encoding='utf-8')
        for line in rules_text.splitlines():
            if line.startswith('# 版本: v'):
                version = line.strip().split('v')[-1]
                break

        now = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'learned_rules_v{version}_{now}.txt'
        backup_path = VERSIONS_DIR / backup_name

        shutil.copy2(RULES_FILE, backup_path)
        logger.debug(f"📦 已备份规则: {backup_name}")

        # 清理旧版本（保留最近 N 个）
        self._cleanup_versions()

    def _cleanup_versions(self):
        """清理旧版本，保留最近 max_versions 个"""
        if not VERSIONS_DIR.exists():
            return
        versions = sorted(VERSIONS_DIR.glob('learned_rules_*.txt'))
        while len(versions) > self.max_versions:
            oldest = versions.pop(0)
            oldest.unlink()
            logger.debug(f"🗑️ 删除旧版本: {oldest.name}")

    def _save_new_rules(self, new_rules: str):
        """保存新规则"""
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        RULES_FILE.write_text(new_rules, encoding='utf-8')
        logger.debug(f"💾 已保存新规则到 {RULES_FILE}")

    def _mark_processed(self, errors: List[Dict]):
        """标记错误报告为已处理"""
        if not ERROR_FILE.exists():
            return

        processed_ids = {e['id'] for e in errors if 'id' in e}
        lines = []
        with open(ERROR_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get('id') in processed_ids:
                        e['processed'] = True
                        e['processed_at'] = datetime.now().isoformat(timespec='seconds')
                    lines.append(json.dumps(e, ensure_ascii=False))
                except json.JSONDecodeError:
                    lines.append(line)

        with open(ERROR_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n' if lines else '')

    def _generate_error_report(self, errors: List[Dict], old_rules: str, new_rules: str):
        """生成人类可读的 Markdown 报告"""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        lines = [
            f'# 邮件分类误分类报告',
            f'生成时间: {now}',
            f'',
            f'## 本次处理了 {len(errors)} 条误分类',
            f'',
        ]

        for i, e in enumerate(errors, 1):
            subject = e.get('email_subject', '?')[:50]
            note = e.get('reporter_note', '')
            lines.append(f'### {i}. {subject}')
            lines.append(f'- 主题: {e.get("email_subject", "?")}')
            lines.append(f'- 发件人: {e.get("email_sender", "?")}')
            lines.append(f'- 错误: Level {e.get("wrong_level", "?")} ({e.get("wrong_sub_category", "?")})')
            lines.append(f'- 正确: Level {e.get("correct_level", "?")} ({e.get("correct_sub_category", "?")})')
            if note:
                lines.append(f'- 备注: {note}')
            lines.append('')

        lines.append('## 规则变更')
        lines.append('')
        lines.append('### 旧规则')
        lines.append('```')
        lines.append(old_rules[:500] if old_rules else '(无)')
        lines.append('```')
        lines.append('')
        lines.append('### 新规则')
        lines.append('```')
        lines.append(new_rules[:1000])
        lines.append('```')
        lines.append('')
        lines.append('## OpenClaw 审阅备注')
        lines.append('')
        lines.append('可以将此文件交给 OpenClaw (Claude Code) 进行人工审阅，')
        lines.append('获得更深入的分析和规则建议。')

        ERROR_REPORT.write_text('\n'.join(lines), encoding='utf-8')
        logger.info(f"📝 误分类报告已保存: {ERROR_REPORT}")

    # ================================================================
    #  回滚
    # ================================================================

    def rollback(self) -> bool:
        """回滚到上一版本规则"""
        if not VERSIONS_DIR.exists():
            print('  [!] 没有可回滚的版本')
            return False

        versions = sorted(VERSIONS_DIR.glob('learned_rules_*.txt'))
        if not versions:
            print('  [!] 没有可回滚的版本')
            return False

        latest = versions[-1]
        # 先备份当前
        if RULES_FILE.exists():
            self._backup_current_rules()

        shutil.copy2(latest, RULES_FILE)
        print(f'  ✅ 已回滚到: {latest.name}')
        return True

    # ================================================================
    #  状态显示
    # ================================================================

    def show_status(self):
        """显示当前状态"""
        errors = self._load_unprocessed_errors()
        all_errors = []
        if ERROR_FILE.exists():
            with open(ERROR_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            all_errors.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        rules_text = self._load_current_rules()
        rule_count = 0
        version = 'v0'
        last_update = '(从未)'
        for line in rules_text.splitlines():
            if line.startswith('# 版本: v'):
                version = line.split('v')[-1].strip()
            if line.startswith('# 最后更新:'):
                last_update = line.split(':', 1)[-1].strip()
            if line.strip() and not line.startswith('#') and re.match(r'^\d', line.strip()):
                rule_count += 1

        versions = sorted(VERSIONS_DIR.glob('learned_rules_*.txt')) if VERSIONS_DIR.exists() else []

        print(f'\n  === 提示词优化引擎状态 ===')
        print(f'  启用状态:     {"✅ 已启用" if self.enabled else "❌ 已禁用"}')
        print(f'  自动优化:     {"✅ 开启" if self.auto_optimize else "❌ 关闭"}')
        print(f'  优化 API:     {self.model} @ {self.api_base_url}')
        print(f'  补充规则:     v{version} ({rule_count} 条)')
        print(f'  最后更新:     {last_update}')
        print(f'  待处理报错:   {len(errors)} 条')
        print(f'  历史报错:     {len(all_errors)} 条')
        print(f'  规则版本数:   {len(versions)} 个')
        print(f'  最大规则数:   {self.max_rules}')
        print(f'  最大版本数:   {self.max_versions}')
        print()


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s',
        handlers=[logging.StreamHandler()]
    )

    args = sys.argv[1:]
    optimizer = PromptOptimizer()

    if '--status' in args:
        optimizer.show_status()
    elif '--dry-run' in args:
        optimizer.optimize(dry_run=True)
    elif '--rollback' in args:
        optimizer.rollback()
    elif '--help' in args or '-h' in args:
        print('''
  用法:
    python _prompt_optimizer.py              手动触发优化
    python _prompt_optimizer.py --dry-run    只分析不保存
    python _prompt_optimizer.py --status     查看当前状态
    python _prompt_optimizer.py --rollback   回滚到上一版本
''')
    else:
        optimizer.optimize()


if __name__ == '__main__':
    main()
