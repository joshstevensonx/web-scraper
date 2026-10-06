#!/bin/bash
# Web Scraper installer for macOS.
#
#   ./install.sh                 install (asks about optional extras)
#   ./install.sh --yes           install without questions (no optional extras)
#   ./install.sh --with-browser  also install JavaScript rendering (Playwright + Chromium, ~150 MB)
#   ./install.sh --with-parquet  also install Parquet output support (pyarrow)
set -euo pipefail
cd "$(dirname "$0")"

ASK=1
WITH_BROWSER=0
WITH_PARQUET=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASK=0 ;;
    --with-browser) WITH_BROWSER=1 ;;
    --with-parquet) WITH_PARQUET=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

bold() { printf "\033[1m%s\033[0m\n" "$1"; }
bold "Web Scraper — installer"

# 1. Find Python 3.9+ (Homebrew, python.org or the Xcode Command Line Tools).
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 /usr/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PY" ]; then
  echo
  echo "Python 3.9 or newer was not found. Install it with ONE of:"
  echo "  xcode-select --install        (Apple's Command Line Tools, includes python3)"
  echo "  brew install python           (Homebrew — https://brew.sh)"
  echo "  https://www.python.org/downloads/macos/"
  echo "Then run ./install.sh again."
  exit 1
fi
echo "Using $("$PY" --version) at $PY"

# 2. Virtual environment with the app and its dependencies.
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment in .venv …"
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip setuptools wheel
echo "Installing Web Scraper and dependencies …"
.venv/bin/python -m pip install --quiet -e ".[dev]"

# 3. Optional extras.
if [ "$ASK" = 1 ] && [ "$WITH_BROWSER" = 0 ] && [ -t 0 ]; then
  read -r -p "Install JavaScript rendering for dynamic websites (Playwright + Chromium, ~150 MB)? [y/N] " a || true
  case "$a" in [yY]*) WITH_BROWSER=1 ;; esac
fi
if [ "$WITH_BROWSER" = 1 ]; then
  echo "Installing Playwright + Chromium …"
  .venv/bin/python -m pip install --quiet "playwright>=1.40"
  .venv/bin/python -m playwright install chromium
fi
if [ "$ASK" = 1 ] && [ "$WITH_PARQUET" = 0 ] && [ -t 0 ]; then
  read -r -p "Install Parquet export support (pyarrow)? [y/N] " a || true
  case "$a" in [yY]*) WITH_PARQUET=1 ;; esac
fi
if [ "$WITH_PARQUET" = 1 ]; then
  .venv/bin/python -m pip install --quiet "pyarrow>=12"
fi

chmod +x "Web Scraper.command" 2>/dev/null || true
.venv/bin/webscraper doctor || true

echo
bold "Done!"
echo "  • Double-click “Web Scraper.command” in Finder to open the app, or run:"
echo "      .venv/bin/webscraper ui"
echo "  • Command line:  .venv/bin/webscraper --help"
echo "  • Results are saved in ~/WebScraper/output"
