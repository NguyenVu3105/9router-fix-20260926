#!/usr/bin/env python3
"""Apply the local macOS/Homebrew stability fixes to a 9Router source checkout.

Run from this checkout with:
        python3 9router-fix-20260926/reapply-fix.py

For a backup copy of this script, pass the checkout explicitly:
        python3 reapply-fix.py --root /path/to/9router
"""
import argparse
import pathlib
import subprocess
import sys

DEFAULT_ROOT = pathlib.Path(__file__).resolve().parent.parent

KEEPALIVE_FIXED = """<key>KeepAlive</key>
        <dict>
                <key>SuccessfulExit</key>
                <false/>
        </dict>"""

# Do not let the process cleanup kill this launcher or its parent shell.
PPID_OLD = "pid !== process.pid.toString()) {"
PPID_NEW = "pid !== process.pid.toString() && pid !== process.ppid.toString()) {"
PPID_WINDOWS_OLD = 'match[1] !== process.pid.toString()) {'
PPID_WINDOWS_NEW = 'match[1] !== process.pid.toString() && match[1] !== process.ppid.toString()) {'

# Include the terminating signal in restart diagnostics.
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

# Keep the Next.js child identifiable without matching unrelated next-server processes.
TITLE_OLD = "process.title = `next-server "
TITLE_NEW = "process.title = `9router-server "
NEXTKILL_OLD = '|| cmd.includes("next-server");'
NEXTKILL_NEW = '|| cmd.includes("9router-server");'

KEEPALIVE_OLD = "<key>KeepAlive</key>\n    <false/>"
KEEPALIVE_TRUE = "<key>KeepAlive</key>\n    <true/>"


def patch_file(path, pairs, label):
    if not path.exists():
        print(f"  [FAIL] {label}: {path} không tồn tại (đổi version/đường dẫn?)")
        return False
    s = path.read_text(encoding="utf-8")
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
    if s != path.read_text(encoding="utf-8"):
        path.write_text(s, encoding="utf-8")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT,
                        help="đường dẫn source checkout (mặc định: thư mục repo chứa script)")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    required = (root / "cli/cli.js", root / "cli/src/cli/tray/autostart.js")
    if not all(path.is_file() for path in required):
        print(f"[FAIL] {root} không phải source checkout 9Router phù hợp.")
        print("       Cần có cli/cli.js và cli/src/cli/tray/autostart.js; dùng --root để chỉ định.")
        return 2

    cli = root / "cli/cli.js"
    autostart = root / "cli/src/cli/tray/autostart.js"
    custom_server = root / "custom-server.js"
    start_server = root / "node_modules/next/dist/server/lib/start-server.js"
    plist = pathlib.Path.home() / "Library/LaunchAgents/com.9router.autostart.plist"

    print(f"Áp patch dành cho macOS/Homebrew vào source: {root}")
    ok = True
    ok &= patch_file(cli, [
        (PPID_OLD, PPID_NEW, "parent-pid guard"),
        (PPID_WINDOWS_OLD, PPID_WINDOWS_NEW, "Windows parent-pid guard"),
        (SIGNAL_OLD, SIGNAL_NEW, "close-signal"),
        (SIGNAL_LOG_OLD, SIGNAL_LOG_NEW, "signal-log"),
        (NEXTKILL_OLD, NEXTKILL_NEW, "server-process whitelist"),
    ], "cli/cli.js")

    keepalive_pairs = [
        (KEEPALIVE_OLD, KEEPALIVE_FIXED, "KeepAlive crash restart"),
        (KEEPALIVE_TRUE, KEEPALIVE_FIXED, "KeepAlive legacy cleanup"),
    ]
    ok &= patch_file(autostart, keepalive_pairs, "cli/src/cli/tray/autostart.js")
    if plist.exists():
        ok &= patch_file(plist, keepalive_pairs, "LaunchAgent plist")
    else:
        print(f"  [skip] plist: chưa được tạo ({plist})")

    if start_server.exists():
        ok &= patch_file(start_server, [(TITLE_OLD, TITLE_NEW, "server process title")], "Next start-server.js")
    else:
        print(f"  [skip] Next start-server.js chưa có trong {root}/node_modules")

    if custom_server.is_file():
        print("  [skip] custom-server.js: giữ nguyên source hiện tại, không copy bản patch cũ")

    files_to_check = [cli, autostart, custom_server]
    if start_server.exists():
        files_to_check.append(start_server)
    for f in files_to_check:
        r = subprocess.run(["node", "--check", str(f)], capture_output=True)
        if r.returncode != 0:
            print(f"  [FAIL] syntax {f.name}: {r.stderr.decode()}")
            ok = False
        else:
            print(f"  [ok]   syntax {f.name}")
    if plist.exists():
        r = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True)
        message = (r.stdout + r.stderr).strip()
        print(("  [ok]   " if r.returncode == 0 else "  [FAIL] ") + (message or str(plist)))
        ok &= r.returncode == 0

    print("HOÀN TẤT" if ok else "CÓ LỖI — xem trên")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
