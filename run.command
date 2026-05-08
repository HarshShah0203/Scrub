#!/bin/bash
# Double-click in Finder to launch the app.
set -e
cd "$(dirname "$0")"

# Pick the best Python interpreter available.
# Apple's bundled /usr/bin/python3 (3.9) ships a stale Tcl/Tk that aborts on
# recent macOS, so we prefer Homebrew Python 3.12/3.13 when present.
pick_python() {
    for candidate in \
        /opt/homebrew/bin/python3.13 \
        /opt/homebrew/bin/python3.12 \
        /opt/homebrew/bin/python3.11 \
        /opt/homebrew/bin/python3 \
        /usr/local/bin/python3.13 \
        /usr/local/bin/python3.12 \
        /usr/local/bin/python3.11 \
        /usr/local/bin/python3; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    # Last resort: Apple's bundled Python (Tk may not work on newer macOS).
    command -v python3 || return 1
}

PY_BOOT="$(pick_python)"
if [ -z "$PY_BOOT" ]; then
    echo "No Python 3 found. Install one with:"
    echo "  brew install python@3.12 python-tk@3.12"
    exit 1
fi

# (Re)build the venv if missing OR if the current one was built with Apple's
# Python 3.9 (known-bad Tk on recent macOS).
VENV_PY=".venv/bin/python"
NEED_VENV=0
if [ ! -x "$VENV_PY" ]; then
    NEED_VENV=1
else
    VER="$("$VENV_PY" -c 'import sys; print(sys.version_info.minor)')"
    if [ "$VER" = "9" ]; then
        echo "Rebuilding venv on modern Python (current one is 3.9, stale Tk)."
        rm -rf .venv
        NEED_VENV=1
    fi
fi

if [ "$NEED_VENV" = "1" ]; then
    echo "Creating virtualenv at .venv using $PY_BOOT ..."
    "$PY_BOOT" -m venv .venv
fi

"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r requirements.txt >/dev/null

# If Tk is not importable, warn (common on Homebrew without python-tk).
if ! "$VENV_PY" -c "import tkinter" 2>/dev/null; then
    echo "WARNING: tkinter not available. Install it with:"
    echo "  brew install python-tk@3.12"
    echo "Falling back to the Gradio web UI."
    exec "$VENV_PY" app.py
fi

exec "$VENV_PY" tk_app.py
