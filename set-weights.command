#!/bin/bash
# Double-click me (or use Settings → Edit assessment weights) to set what
# each assessment is worth.
cd "$(dirname "${BASH_SOURCE[0]}")"
./.venv/bin/python -m canvasdates.cli weights
echo
read -n 1 -s -r -p "Done. Press any key to close this window."
