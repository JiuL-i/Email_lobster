"""Persistent seen-email store for tracking which messages have been
fetched, pushed in summaries, and notified via private messages.

Storage: JSONL at state/seen_emails.jsonl. One record per line so we can
stream-read without loading everything into memory and survive crashes
mid-write.

Record schema:
  {
    "mailbox":       <source_email>,
    "id":            <Message-ID from RFC 822 header>,
    "subject":       <truncated subject>,
    "owner":         <resolved owner>,
    "level":         <int 0-6>,
    "platform":      <WMS/AMZ/TEM/...>,
    "category":      <sub_category>,
    "first_seen":    <ISO8601 timestamp>,
    "pushed_slots":  [<HH:MM slot strings>],
    "private_notified": <bool>,
    "notify_owner":  <owner used for private push; '' if not notified>,
  }

Lookups are by (mailbox, id); the JSONL is replayed into an in-memory
dict on first access (cheap — typical mailbox produces a few thousand
entries per month).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_STATE_DIR = Path("state")
DEFAULT_FILE = DEFAULT_STATE_DIR / "seen_emails.jsonl"


class SeenEmailsStore:
    """Thread-safe JSONL-backed store of seen/pushed emails."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # (mailbox, id) -> record dict
        self._index: Dict[Tuple[str, str], dict] = {}
        self._loaded = False

    # ---- I/O ----
    def _load(self) -> None:
        if self._loaded:
            return
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        key = (rec.get("mailbox", ""), rec.get("id", ""))
                        if key[0] and key[1]:
                            # Merge: latest pushed_slots + private_notified wins.
                            existing = self._index.get(key)
                            if existing:
                                merged_slots = sorted(
                                    set(existing.get("pushed_slots", []))
                                    | set(rec.get("pushed_slots", []))
                                )
                                rec["pushed_slots"] = merged_slots
                                if rec.get("private_notified"):
                                    existing["private_notified"] = True
                                    existing["notify_owner"] = rec.get("notify_owner", "")
                            self._index[key] = rec if not existing else existing
            except OSError:
                pass
        self._loaded = True

    def _append(self, record: dict) -> None:
        """Append a single record; safe under concurrent writes from
        daemon + launcher via the global lock."""
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _rewrite(self) -> None:
        """Rewrite the file from current index (used after merge)."""
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for rec in self._index.values():
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    # ---- API ----
    def is_seen(self, mailbox: str, msg_id: str) -> bool:
        if not mailbox or not msg_id:
            return False
        with self._lock:
            self._load()
            return (mailbox, msg_id) in self._index

    def mark_seen(
        self,
        mailbox: str,
        msg_id: str,
        *,
        subject: str = "",
        owner: str = "",
        level: int = 6,
        platform: str = "",
        category: str = "",
        notify_owner: str = "",
    ) -> bool:
        """Record that we saw this email. Returns True if it was new."""
        if not mailbox or not msg_id:
            return False
        with self._lock:
            self._load()
            key = (mailbox, msg_id)
            existing = self._index.get(key)
            if existing is not None:
                # Backfill any missing fields from the new fetch.
                for f in ("subject", "owner", "level", "platform", "category", "notify_owner"):
                    if not existing.get(f) and locals().get(f):
                        existing[f] = locals()[f]
                return False
            rec = {
                "mailbox": mailbox,
                "id": msg_id,
                "subject": subject[:120],
                "owner": owner,
                "level": int(level) if isinstance(level, (int, float)) else 6,
                "platform": platform,
                "category": category,
                "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "pushed_slots": [],
                "private_notified": False,
                "notify_owner": notify_owner,
            }
            self._index[key] = rec
            self._append(rec)
            return True

    def mark_pushed_in_slot(
        self,
        mailbox: str,
        msg_id: str,
        slot: str,
        *,
        private_notified: Optional[bool] = None,
        notify_owner: Optional[str] = None,
    ) -> None:
        if not mailbox or not msg_id or not slot:
            return
        with self._lock:
            self._load()
            rec = self._index.get((mailbox, msg_id))
            if rec is None:
                # Auto-create a minimal record so we can mark pushed even
                # if mark_seen wasn't called (defensive).
                rec = {
                    "mailbox": mailbox,
                    "id": msg_id,
                    "subject": "",
                    "owner": "",
                    "level": 6,
                    "platform": "",
                    "category": "",
                    "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "pushed_slots": [],
                    "private_notified": False,
                    "notify_owner": "",
                }
                self._index[(mailbox, msg_id)] = rec
                self._append(rec)
            slots = set(rec.get("pushed_slots", []) or [])
            slots.add(slot)
            rec["pushed_slots"] = sorted(slots)
            if private_notified is not None:
                rec["private_notified"] = bool(private_notified)
            if notify_owner is not None:
                rec["notify_owner"] = notify_owner
            self._rewrite()

    def get_unpushed_for_slot(self, slot: str) -> List[dict]:
        """Return records whose `pushed_slots` does NOT contain `slot`."""
        if not slot:
            return []
        with self._lock:
            self._load()
            return [
                dict(rec)
                for rec in self._index.values()
                if slot not in (rec.get("pushed_slots") or [])
            ]

    def all_seen(self) -> List[dict]:
        with self._lock:
            self._load()
            return [dict(rec) for rec in self._index.values()]

    def stats(self) -> dict:
        with self._lock:
            self._load()
            return {
                "total_records": len(self._index),
                "never_pushed": sum(
                    1 for r in self._index.values() if not r.get("pushed_slots")
                ),
            }