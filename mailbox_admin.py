"""Atomic mailbox_mapping.mailboxes management helpers.

Used by both the CLI (--list/--add/--remove) and the controller's HTTP API.
Reads and writes config_imap_forward.json using the same atomic patterns as
save_to_cycle_excel (tempfile + os.replace) and the same race-safe retry used
by _check_config_reload for half-written JSON files.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


CONFIG_FILE = "config_imap_forward.json"
PLACEHOLDER_MARKERS = ("你的QQ邮箱", "your_email", "your@email", "你的邮箱")

# Valid notify_channel values — must match the daemon's _get_owner_channel switch.
VALID_NOTIFY_CHANNELS = ("group", "private", "both")
DEFAULT_NOTIFY_CHANNEL = "private"


def config_path(base: Optional[os.PathLike[str] | str] = None) -> Path:
    if base is None:
        return Path(CONFIG_FILE).resolve()
    return Path(base) / CONFIG_FILE


def load_config(path: Path, retries: int = 3) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for _ in range(retries):
        try:
            with path.open("r", encoding="utf-8") as stream:
                return json.load(stream)
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(0.5)
        except FileNotFoundError:
            raise
    raise json.JSONDecodeError("config half-written after retries", "", 0) from last_error


def save_config(path: Path, config: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".config-", dir=str(path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def list_mailboxes(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    mapping = config.setdefault("mailbox_mapping", {})
    return list(mapping.get("mailboxes", []))


def is_first_run(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return True
    return any(marker in text for marker in PLACEHOLDER_MARKERS)


def add_mailbox(config: Dict[str, Any], entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    mapping = config.setdefault("mailbox_mapping", {})
    mailboxes: List[Dict[str, Any]] = list(mapping.get("mailboxes", []))
    source = (entry.get("source_email") or "").strip()
    owner = (entry.get("owner") or "").strip()
    if not source or not owner:
        raise ValueError("source_email and owner are required")
    if any(m.get("source_email", "").lower() == source.lower() for m in mailboxes):
        raise ValueError(f"mailbox {source} already exists")
    raw_channel = entry.get("notify_channel")
    if raw_channel is None or (isinstance(raw_channel, str) and raw_channel.strip() == ""):
        notify_channel = mapping.get("default_channel") or DEFAULT_NOTIFY_CHANNEL
    else:
        notify_channel = raw_channel.strip()
        if notify_channel not in VALID_NOTIFY_CHANNELS:
            raise ValueError(
                f"invalid notify_channel {notify_channel!r}; allowed values: {VALID_NOTIFY_CHANNELS}"
            )
    new_entry = {"source_email": source, "owner": owner, "notify_channel": notify_channel}
    prefix = (entry.get("prefix") or "").strip()
    if prefix:
        new_entry["prefix"] = prefix
    mailboxes.append(new_entry)
    mapping["mailboxes"] = mailboxes
    return mailboxes


def remove_mailbox(config: Dict[str, Any], source_email: str) -> List[Dict[str, Any]]:
    mapping = config.setdefault("mailbox_mapping", {})
    mailboxes: List[Dict[str, Any]] = list(mapping.get("mailboxes", []))
    target = source_email.strip().lower()
    kept = [m for m in mailboxes if (m.get("source_email", "") or "").lower() != target]
    if len(kept) == len(mailboxes):
        raise ValueError(f"mailbox {source_email} not found")
    mapping["mailboxes"] = kept
    return kept


def _load_from_argv() -> Dict[str, Any]:
    args = sys.argv[1:]
    cfg_path = CONFIG_FILE
    for arg in args:
        if arg.startswith("--config="):
            cfg_path = arg.split("=", 1)[1]
    return {"config_path": Path(cfg_path).resolve()}


def main() -> int:
    args = sys.argv[1:]
    cfg_path = config_path()
    action: Optional[str] = None
    payload: Dict[str, Any] = {}

    for arg in args:
        if arg in ("--list", "--add", "--remove") and action is None:
            action = arg.lstrip("-")
        elif arg.startswith("--config="):
            cfg_path = Path(arg.split("=", 1)[1]).resolve()

    if "--json" in args:
        idx = args.index("--json")
        payload = json.loads(args[idx + 1])

    if action is None or action == "--help" or action == "-h":
        print("Usage: mailbox_admin.py --list | --add --json {...} | --remove <email>")
        return 0

    if action == "list":
        if not cfg_path.exists():
            print("[]")
            return 0
        config = load_config(cfg_path)
        print(json.dumps(list_mailboxes(config), ensure_ascii=False, indent=2))
        return 0

    if action == "add":
        config = load_config(cfg_path) if cfg_path.exists() else {"mailbox_mapping": {"mailboxes": []}}
        try:
            mailboxes = add_mailbox(config, payload)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        save_config(cfg_path, config)
        print(json.dumps(mailboxes, ensure_ascii=False, indent=2))
        return 0

    if action == "remove":
        target = payload.get("source_email") or next(
            (a for a in args if not a.startswith("--") and "@" in a), ""
        )
        if not target:
            print("error: source_email required", file=sys.stderr)
            return 1
        config = load_config(cfg_path)
        try:
            mailboxes = remove_mailbox(config, target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        save_config(cfg_path, config)
        print(json.dumps(mailboxes, ensure_ascii=False, indent=2))
        return 0

    print(f"unknown action: {action}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())