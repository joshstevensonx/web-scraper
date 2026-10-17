#!/bin/bash
# Double-click this file in Finder to start Web Scraper (opens in your browser).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/webscraper ]; then
  echo "First run: installing Web Scraper (this takes a minute)…"
  if ! ./install.sh; then
    echo
    read -n 1 -s -r -p "Installation failed. Press any key to close."
    exit 1
  fi
fi
echo "Starting Web Scraper… (close this window or press Ctrl+C to stop)"
exec .venv/bin/webscraper ui
