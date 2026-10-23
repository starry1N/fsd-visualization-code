#!/bin/bash
# Build only: never starts the receiver, touches hardware, or runs tests.
set -euo pipefail
if [[ "$(uname -s)" != Darwin ]]; then
    echo 'Build on macOS, or use the macOS receiver GitHub Actions workflow.' >&2
    exit 1
fi
radio_dir="$(cd -- "$(dirname "$0")" && pwd)"
output_dir="${1:-$radio_dir/dist}"
mkdir -p "$output_dir" "$radio_dir/build/macos"
output_dir="$(cd -- "$output_dir" && pwd)"
"${PYTHON:-python3}" -m venv "$radio_dir/.venv-macos"
build_python="$radio_dir/.venv-macos/bin/python"
"$build_python" -m pip install -e "${radio_dir}[pc]" 'PyInstaller==6.22.0'
"$build_python" -m PyInstaller --noconfirm --distpath "$radio_dir/build/macos/dist" \
    --workpath "$radio_dir/build/macos/cache" "$radio_dir/windows_receiver.spec"
architecture="$(uname -m)"
case "$architecture" in
    arm64|x86_64) ;;
    *) echo "Unsupported architecture: $architecture" >&2; exit 1 ;;
esac
bundle_name="FSD_E22_Receiver-macOS-$architecture"
staging_dir="$(mktemp -d "$radio_dir/build/macos/package.XXXXXX")"
mkdir "$staging_dir/$bundle_name"
cp "$radio_dir/build/macos/dist/FSD_E22_Receiver" "$staging_dir/$bundle_name/"
cp "$radio_dir/start_macos.command" "$staging_dir/$bundle_name/Start_Receiver.command"
cp "$radio_dir/foxglove_layout.json" "$staging_dir/$bundle_name/FSD_E22_CLI.json"
cp "$radio_dir/MACOS_README.md" "$staging_dir/$bundle_name/README.md"
chmod 755 "$staging_dir/$bundle_name/FSD_E22_Receiver" "$staging_dir/$bundle_name/Start_Receiver.command"
{
    git -C "$radio_dir" rev-parse HEAD
    uname -m
    sw_vers
    "$build_python" --version
} > "$staging_dir/$bundle_name/BUILD_INFO.txt"
tar -czf "$output_dir/$bundle_name.tar.gz" -C "$staging_dir" "$bundle_name"
(cd "$output_dir" && shasum -a 256 "$bundle_name.tar.gz" > "$bundle_name.tar.gz.sha256")
echo "Built $output_dir/$bundle_name.tar.gz"
