#!/usr/bin/env python3
"""Re-apply local 9router stability fixes (mất khi npm update 9router).

Chạy: python3 ~/.9router/backups/9router-fix-20260926/reapply-fix.py
"""
import pathlib
import subprocess
import sys

PKG = pathlib.Path("/opt/homebrew/lib/node_modules/9router")
CLI = PKG / "cli.js"
AUTOSTART = PKG / "src/cli/tray/autostart.js"
PLIST = pathlib.Path.home() / "Library/LaunchAgents/com.9router.autostart.plist"
CUSTOM_SERVER = PKG / "app/custom-server.js"
CUSTOM_SERVER_FIXED = pathlib.Path(__file__).parent / "custom-server.js.fixed"

# F1a: tray mode phải gắn attachServerEvents() trước khi return,
# nếu không server chết thì icon tray vẫn hiện mà không restart/không log.
TRAY_OLD = """    });

    return;
  }

  // Wait for server to be ready, then show interface menu loop + tray"""
TRAY_NEW = """    });

    // FIX: monitor server even in tray mode — without this, a dead server
    // leaves a zombie tray icon with no restart and no logs.
    attachServerEvents();
    return;
  }

  // Wait for server to be ready, then show interface menu loop + tray"""

# F2b: killAllAppProcesses không được kill process cha (bug #4255).
PPID_OLD = "pid !== process.pid.toString()) {"
PPID_NEW = "pid !== process.pid.toString() && pid !== process.ppid.toString()) {"

# F5: log cả signal khi server chết (biết rõ nguyên nhân crash thay vì "code=unknown").
SIGNAL_OLD = '''    server.on("close", (code) => {
      if (isShuttingDown || code === 0) {
        process.exit(code || 0);
        return;
      }
      tryRestart(code);
    });
  }

  function tryRestart(code) {'''
SIGNAL_NEW = '''    server.on("close", (code, signal) => {
      if (isShuttingDown || code === 0) {
        process.exit(code || 0);
        return;
      }
      tryRestart(code, signal);
    });
  }

  function tryRestart(code, signal) {'''
SIGNAL_LOG_OLD = 'console.error(`\\n⚠️  Server exited (code=${code ?? "unknown"}). Restarting in ${delay / 1000}s... (${restartCount}/${MAX_RESTARTS})`);'
SIGNAL_LOG_NEW = 'console.error(`\\n⚠️  Server exited (code=${code ?? "unknown"}${signal ? `, signal=${signal}` : ""}). Restarting in ${delay / 1000}s... (${restartCount}/${MAX_RESTARTS})`);'

# F3: launchd tự khởi động lại sau khi bị kill/exit.
# F8: phải là dict SuccessfulExit=false — xem KEEPALIVE_FIXED bên dưới.
# Giữ pattern <true/> ở đây để dọn bản cũ đã patch trước đó.
KEEPALIVE_OLD = "<key>KeepAlive</key>\n    <false/>"
KEEPALIVE_TRUE = "<key>KeepAlive</key>\n    <true/>"

# F6: đổi process.title của server child → `9router-server` thay vì `next-server`,
# để agent TaskFlow (pkill -9 -f next-server trước mỗi lượt Playwright) không kill
# nhầm 9router. F6b: killAllAppProcesses chỉ whitelist "9router-server" (không còn
# kill next-server của project khác khi tray khởi động).
START_SERVER = PKG / "app/node_modules/next/dist/server/lib/start-server.js"
TITLE_OLD = "process.title = `next-server "
TITLE_NEW = "process.title = `9router-server "
NEXTKILL_OLD = '|| cmd.includes("next-server");'
NEXTKILL_NEW = '|| cmd.includes("9router-server");'

# F8: KeepAlive phải là dict {SuccessfulExit:false} chứ không phải <true/>.
# KeepAlive=true khiến launchd bật lại 9router NGAY CẢ khi bấm Quit (exit 0),
# nên không tắt được. SuccessfulExit=false => chỉ tự bật lại khi crash
# (exit code khác 0), còn Quit thì exit 0 => launchd không khởi động lại.
KEEPALIVE_FIXED = """<key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>"""


def patch_file(path, pairs, label):
    if not path.exists():
        print(f"  [FAIL] {label}: {path} không tồn tại (đổi version/đường dẫn?)")
        return False
    s = path.read_text()
    ok = True
    for old, new, name in pairs:
        if new in s:
            print(f"  [skip] {label} {name}: đã patch")
            continue
        if old not in s:
            print(f"  [FAIL] {label} {name}: không tìm thấy pattern — patch thủ công!")
            ok = False
            continue
        s = s.replace(old, new)
        print(f"  [ok]   {label} {name}")
    path.write_text(s)
    return ok


def main():
    print("Áp patch 9router:")
    ok = True
    ok &= patch_file(CLI, [(TRAY_OLD, TRAY_NEW, "F1a tray-attach"), (PPID_OLD, PPID_NEW, "F2b ppid"),
                           (SIGNAL_OLD, SIGNAL_NEW, "F5a close-signal"), (SIGNAL_LOG_OLD, SIGNAL_LOG_NEW, "F5b signal-log"),
                           (NEXTKILL_OLD, NEXTKILL_NEW, "F6b kill-whitelist")], "cli.js")
    ok &= patch_file(START_SERVER, [(TITLE_OLD, TITLE_NEW, "F6 title")], "start-server.js")
    ok &= patch_file(AUTOSTART, [(KEEPALIVE_OLD, KEEPALIVE_FIXED, "F3b+F8 template")], "autostart.js")
    ok &= patch_file(PLIST, [(KEEPALIVE_OLD, KEEPALIVE_FIXED, "F3a+F8 plist")], "plist")
    # Dọn bản <true/> do patch cũ (F3) từng ghi — nay phải thay bằng dict.
    for p, lbl in ((AUTOSTART, "autostart.js"), (PLIST, "plist")):
        if not p.exists():
            continue
        s = p.read_text()
        if KEEPALIVE_TRUE in s:
            p.write_text(s.replace(KEEPALIVE_TRUE, KEEPALIVE_FIXED))
            print(f"  [ok]   {lbl} F8: KeepAlive <true/> → SuccessfulExit=false")

    # F4: custom-server.js — /v1/messages fix (OpenAI→Anthropic non-stream,
    # max_tokens bump cho auto-mode classifier, debug log /tmp/9r-messages.log).
    # Copy nguyên file đã patch (quá nhiều thay đổi để diff).
    if not CUSTOM_SERVER_FIXED.exists():
        print(f"  [FAIL] F4: thiếu {CUSTOM_SERVER_FIXED}")
        ok = False
    elif not CUSTOM_SERVER.exists():
        print(f"  [FAIL] F4: {CUSTOM_SERVER} không tồn tại")
        ok = False
    elif CUSTOM_SERVER.read_bytes() == CUSTOM_SERVER_FIXED.read_bytes():
        print("  [skip] F4 custom-server: đã patch")
    else:
        CUSTOM_SERVER.write_bytes(CUSTOM_SERVER_FIXED.read_bytes())
        print("  [ok]   F4 custom-server: đã copy bản patch")

    for f in (CLI, AUTOSTART, CUSTOM_SERVER, START_SERVER):
        r = subprocess.run(["node", "--check", str(f)], capture_output=True)
        if r.returncode != 0:
            print(f"  [FAIL] syntax {f.name}: {r.stderr.decode()}")
            ok = False
        else:
            print(f"  [ok]   syntax {f.name}")
    r = subprocess.run(["plutil", "-lint", str(PLIST)], capture_output=True)
    print(("  [ok]   " if r.returncode == 0 else "  [FAIL] ") + r.stdout.decode().strip())

    print("HOÀN TẤT" if ok else "CÓ LỖI — xem trên")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
