#!/usr/bin/env bash
set -euo pipefail
RADIO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# ROS 2 Humble Python modules must remain visible; use its system Python 3.10.
if /usr/bin/python3 -c 'import ensurepip' 2>/dev/null; then
    /usr/bin/python3 -m venv --system-site-packages "$RADIO_ROOT/.venv"
elif /usr/bin/python3 -m pip --version >/dev/null 2>&1; then
    # Minimal Ubuntu installs may provide system pip but not python3-venv's ensurepip.
    /usr/bin/python3 -m venv --without-pip --system-site-packages "$RADIO_ROOT/.venv"
else
    printf '%s\n' 'Install python3-venv (or python3-pip) with the system package manager, then retry.' >&2
    exit 2
fi
"$RADIO_ROOT/.venv/bin/python" -m pip install --upgrade pip 'setuptools>=64,<80' wheel
"$RADIO_ROOT/.venv/bin/python" -m pip install -e "$RADIO_ROOT"
printf '%s\n' 'Ready. Run bash start_linux.sh (radio.yaml port), or bash start_linux.sh RADIO_SERIAL_PORT.'
