#!/bin/bash
set -e
receiver_dir="$(cd -- "$(dirname "$0")" && pwd)"
exec "$receiver_dir/FSD_E22_Receiver" "$@"
