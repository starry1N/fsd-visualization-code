#!/usr/bin/env bash
# Source installed environments only; no builds, simulator startup or control.
set -eo pipefail
FSD_VIS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FSD_VIS_ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
[[ -f "$FSD_VIS_ROS_SETUP" ]] || { echo "Missing ROS setup: $FSD_VIS_ROS_SETUP" >&2; return 2; }
source "$FSD_VIS_ROS_SETUP"
if [[ -n "${FSD_SETUP:-}" ]]; then
  [[ -f "$FSD_SETUP" ]] || { echo "Missing FSD_SETUP: $FSD_SETUP" >&2; return 2; }
  source "$FSD_SETUP"
elif ! /usr/bin/python3 -c 'import wuta_msgs.msg, autoware_msgs.msg' 2>/dev/null; then
  # In the main checkout, reuse its existing FSD install without scanning workspaces.
  if [[ -f "$FSD_VIS_ROOT/../WUTA-FSD/ros2_ws/install/setup.bash" ]]; then
    source "$FSD_VIS_ROOT/../WUTA-FSD/ros2_ws/install/setup.bash"
  fi
fi
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
