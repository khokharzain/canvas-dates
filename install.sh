#!/bin/bash
# Canvas Dates — one-shot installer for macOS.
# Creates a private Python environment, saves your Canvas token, and sets the
# menu bar app to start when you log in.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
PLIST="$HOME/Library/LaunchAgents/com.canvasdates.menubar.plist"
LABEL="com.canvasdates.menubar"

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This installer is for macOS." >&2; exit 1
fi

say "1/5  Finding Python"
PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
  [ -x "$candidate" ] || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Need Python 3.9 or newer. Install it with:  xcode-select --install" >&2
  exit 1
fi
echo "     using $PY ($("$PY" -V 2>&1))"

say "2/5  Creating a private environment"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
echo "     dependencies installed"

# python.org builds ship without root certificates, so HTTPS fails until
# they're installed. The installer that ships with Python does it system
# wide; certifi (above) covers this app either way.
for certs in /Applications/Python*/Install\ Certificates.command; do
  [ -x "$certs" ] && "$certs" >/dev/null 2>&1 && \
    echo "     system certificates installed" && break
done

if ! "$VENV/bin/python" -c "
import ssl, sys
sys.path.insert(0, '$HERE')
from canvasdates.icsfeed import ssl_context
sys.exit(0 if len(ssl_context().get_ca_certs()) > 50 else 1)
" 2>/dev/null; then
  echo "     WARNING: no certificate authorities found — HTTPS may fail" >&2
else
  echo "     HTTPS certificates OK"
fi

say "3/5  Connecting to Canvas"
if "$VENV/bin/python" -c "
import sys; sys.path.insert(0, '$HERE')
from canvasdates import config, icsfeed
url = config.get_feed()
if not url: sys.exit(1)
try:
    icsfeed.download(url)
except Exception: sys.exit(1)
" 2>/dev/null; then
  echo "     already connected — keeping your existing calendar feed"
else
  ( cd "$HERE" && "$VENV/bin/python" -m canvasdates.cli setup )
fi

say "4/5  Starting at login"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>-m</string>
    <string>canvasdates</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$HERE/canvas-dates.log</string>
  <key>StandardErrorPath</key><string>$HERE/canvas-dates.log</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "     login item installed"

say "5/5  Checking everything works"
( cd "$HERE" && "$VENV/bin/python" -m canvasdates.cli list ) || true

cat <<'DONE'

Done. Look at the right-hand side of your menu bar — you should see
something like  🟡 CAB444 6d · 30%

  Refresh, settings, "Mark as done" and quitting all live in that dropdown.

  Percentages come from you, not Canvas — QUT blocks the API that would
  carry them. To enter or change them, double-click set-weights.command
  in this folder, or use Settings -> Edit assessment weights.

  Useful commands (run from this folder):
    ./install.sh                                re-run setup
    ./set-weights.command                       enter assessment weights
    .venv/bin/python -m canvasdates.cli list    print what's due
    .venv/bin/python -m canvasdates.cli doctor  diagnose problems
    python3 selftest.py                         offline self-test
    ./uninstall.sh                              remove the login item

DONE
