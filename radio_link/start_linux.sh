#!/usr/bin/env bash
set -euo pipefail
RADIO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$RADIO_ROOT/.venv/bin/python" ]]; then
    printf '%s\n' "Missing radio Python environment. Run: bash $RADIO_ROOT/setup_linux.sh" >&2
    exit 2
fi
# Reuse the viewer's existing ROS/FSD environment loader.
set +u  # ROS/colcon setup scripts reference optional unset variables.
source "$RADIO_ROOT/../scripts/environment.sh"
set -u
PORT_ARGS=()
if [[ $# -gt 0 && "$1" != -* ]]; then
    PORT_ARGS=(--port "$1")
    shift
fi
"$RADIO_ROOT/.venv/bin/python" -c 'import rclpy, tf2_ros, visualization_msgs.msg' || {
    printf '%s\n' 'Source ROS 2 Humble and the existing FSD overlay before launching.' >&2
    exit 2
}
exec "$RADIO_ROOT/.venv/bin/python" -m fsd_radio.cli --config "$RADIO_ROOT/radio.yaml" linux "${PORT_ARGS[@]}" "$@"
