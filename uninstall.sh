#!/bin/bash
# Remove the login item and stop the app. Leaves your files and token alone.
set -euo pipefail
LABEL="com.canvasdates.menubar"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Login item removed. The app will not start again at login."
echo "To also forget your Canvas token:"
echo "  security delete-generic-password -s canvas-dates -a canvas-api-token"
