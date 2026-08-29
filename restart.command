#!/bin/bash
# Double-click to restart the menu bar app after an update.
launchctl kickstart -k "gui/$(id -u)/com.canvasdates.menubar" 2>/dev/null \
  && echo "Canvas Dates restarted." \
  || echo "Not installed as a login item yet — run ./install.sh first."
sleep 1
