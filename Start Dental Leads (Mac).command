#!/bin/bash
# Double-click to start the Dental Leads website. Keep this window open while you use it.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/ and then double-click this file again."
  read -r -p "Press Enter to close."
  exit 1
fi
if [ ! -d .venv ]; then
  echo "First run: setting things up (about a minute)..."
  python3 -m venv .venv || { read -r -p "Could not create the Python environment. Press Enter to close."; exit 1; }
fi
source .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt || { read -r -p "Installing packages failed. Press Enter to close."; exit 1; }
echo "Opening Dental Leads in your browser. Close this window (or press Ctrl+C) to stop it."
python -m leadgen.web "$@"
