"""Mail Lobster 弹窗源头诊断 — 用 Win32 EnumWindows 抓所有顶层窗口"""
import ctypes
import ctypes.wintypes as w
import time
import subprocess
from pathlib import Path

user32 = ctypes.windll.user32

WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

# 全局容器，cb 引用它就安全
GLOBAL_HITS = []


@WNDENUMPROC
def _enum_cb(hwnd, _lparam):
    if not user32.IsWindowVisible(hwnd):
        return True
    # 用 ctypes.create_unicode_buffer 分配内存
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    if not buf.value:
        return True
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    GLOBAL_HITS.append((hwnd, pid.value, buf.value))
    return True


def enum_titles():
    GLOBAL_HITS.clear()
    user32.EnumWindows(_enum_cb, 0)
    return list(GLOBAL_HITS)


print('=== Mail Lobster 弹窗源头诊断 ===')
print('启动 EXE 然后每 0.5s 抓所有有标题的顶层窗口 15 秒...')
print()

EXE = Path(r'D:/邮件管理系统/email_lobster/dist/mail_lobster/mail_lobster.exe')
proc = subprocess.Popen([str(EXE)])
print(f'已启动 EXE, PID={proc.pid}')

start = time.time()
seen = set()
while time.time() - start < 15:
    try:
        titles = enum_titles()
        now = time.strftime('%H:%M:%S')
        new = [(h, p, t) for h, p, t in titles if (h, t) not in seen]
        if new:
            print(f'\n[{now}] 新出现的窗口 ({len(new)}):', flush=True)
            for h, p, t in new:
                print(f'  hwnd=0x{h:08X} pid={p} title: {t!r}', flush=True)
                seen.add((h, t))
        time.sleep(0.5)
    except Exception as e:
        print(f'  err: {e}', flush=True)

print('\n=== 全部检测到的窗口 ===', flush=True)
try:
    all_titles = enum_titles()
    for h, p, t in all_titles:
        print(f'  hwnd=0x{h:08X} pid={p} title: {t!r}', flush=True)
except Exception as e:
    print(f'  err: {e}', flush=True)
