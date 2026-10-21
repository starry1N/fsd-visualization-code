"""Reuse the existing viewer renderer to resolve effective native/bridge topics."""

import importlib.util
import math
from pathlib import Path
import yaml

DISPLAY_NAMES = {
    "guided": "Camera-guided clusters (fusion)",
    "fused": "Fused targets and sources",
    "map": "Cone map",
    "centerline": "Centerline",
    "path": "Final planned path",
    "trajectory": "Driven trajectory",
    "control": "Control target",
}


def load_config(filename, mode=None):
    path = Path(filename).resolve()
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("topics_config", "rviz_config"):
        config[key] = str((path.parent / config[key]).resolve())
    config["display_mode"] = mode or config["display_mode"]
    topics = yaml.safe_load(Path(config["topics_config"]).read_text(encoding="utf-8"))
    template = yaml.safe_load(Path(config["rviz_config"]).read_text(encoding="utf-8"))
    renderer = (
        Path(__file__).resolve().parents[2]
        / "src/fsd_visualization/fsd_visualization/configuration.py"
    )
    # The original pure configuration module has no ROS dependency.
    if not renderer.is_file():
        raise ValueError(
            f"keep the full viewer source directory; renderer missing: {renderer}"
        )
    spec = importlib.util.spec_from_file_location("_fsd_viewer_configuration", renderer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    effective = module.render(template, topics, config["display_mode"])
    displays = effective["Visualization Manager"]["Displays"]
    config["resolved_layers"] = {}
    for key, name in DISPLAY_NAMES.items():
        # Existing bridge renderer gives the fused display a descriptive suffix.
        display = next(
            d
            for d in displays
            if d["Name"] == name
            or (key == "fused" and d["Name"].startswith("Fused targets ("))
        )
        config["resolved_layers"][key] = (
            display["Topic"]["Value"] if display.get("Enabled", False) else ""
        )
    config["fixed_frame"] = topics["fixed_frame"]
    config["pose_topic"] = topics["topics"]["pose"]
    config["target_frame"] = topics["target_frame"]
    config["velocity_topic"] = (
        config.get("velocity_topic") or topics["topics"]["velocity"]
    )
    config["inspection"] = {
        "excluded": {
            "camera": topics["topics"]["camera_image"],
            "raw_lidar": topics["topics"]["raw_lidar"],
        },
        "raw_lidar_template": next(
            d
            for d in template["Visualization Manager"]["Displays"]
            if d["Name"] == "Raw LiDAR (optional)"
        )["Topic"]["Value"],
        "velocity_in_topics_yaml": topics["topics"]["velocity"],
        "effective_marker_topics": config["resolved_layers"],
    }
    cli_defaults = {
        "enabled": True,
        "project_root": "../..",
        "state_file": "../../.runtime/fsd_manager/state.json",
        "selection_file": "../../.runtime/fsd_manager/radio_run.json",
        "telemetry_file": "../../.runtime/fsd_manager/telemetry.json",
        "checkpoint_file": "../../.runtime/fsd_radio/cli_tx.json",
        "pc_storage": "~/.fsd_radio/cli_received",
        "chunk_bytes": 2048,
        "scan_seconds": 0.2,
        "retry_seconds": 3.0,
        "status_interval": 2.0,
    }
    config["cli"] = {**cli_defaults, **config.get("cli", {})}
    for key in (
        "project_root",
        "state_file",
        "selection_file",
        "telemetry_file",
        "checkpoint_file",
        "pc_storage",
    ):
        candidate = Path(config["cli"][key]).expanduser()
        config["cli"][key] = str(
            (path.parent / candidate).resolve()
            if not candidate.is_absolute()
            else candidate.resolve()
        )
    validate(config)
    return config


def validate(config):
    def positive(value, name):
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")

    link = config["link"]
    for name in (
        "bytes_per_second",
        "fragment_bytes",
        "response_timeout",
        "turnaround_seconds",
        "assembly_timeout",
    ):
        positive(link[name], name)
    baud = config["serial"]["baud"]
    if str(config['serial'].get('pc_port', '')).lower() == 'auto':
        usb = config['serial'].get('pc_usb', {'vid': 0x1A86, 'pid': 0x7523})
        if not isinstance(usb, dict):
            raise ValueError('serial.pc_usb must be a USB selector mapping')
        for name in ('vid', 'pid'):
            if type(usb.get(name)) is not int or not 0 <= usb[name] <= 65535:
                raise ValueError(f'serial.pc_usb.{name} must be a 16-bit integer')
        for name in ('serial_number', 'location'):
            if not isinstance(usb.get(name, ''), str):
                raise ValueError(f'serial.pc_usb.{name} must be a string')
    for key in ('append_rssi_pc', 'append_rssi_linux'):
        if type(config['serial'].get(key, False)) is not bool:
            raise ValueError(f'serial.{key} must be boolean')
    module_packet = config['serial'].get('module_packet_bytes', 240)
    if type(module_packet) is not int or module_packet not in (32, 64, 128, 240):
        raise ValueError('serial.module_packet_bytes must be 32, 64, 128 or 240')
    rssi_enabled = any(config['serial'].get(key, False)
                       for key in ('append_rssi_pc', 'append_rssi_linux'))
    if rssi_enabled and module_packet != 240:
        raise ValueError('Appended RSSI parsing requires the E22 module packet setting to be 240 bytes')
    air_rate = link.get("air_rate_bps", 2400)
    if not isinstance(baud, int) or baud <= 0 or not isinstance(air_rate, int) or air_rate <= 0:
        raise ValueError("serial.baud and link.air_rate_bps must be positive integers")
    if link["bytes_per_second"] > 0.8 * min(baud / 10, air_rate / 8):
        raise ValueError(
            "UART aggregate budget exceeds 80% of the configured serial/air-rate ceiling"
        )
    if (
        not isinstance(link["fragment_bytes"], int)
        or not 16 <= link["fragment_bytes"] <= 144
    ):
        raise ValueError("fragment_bytes must be an integer from 16 to 144")
    if not isinstance(link["retries"], int) or not 0 <= link["retries"] <= 5:
        raise ValueError("retries must be 0..5")
    for name in ("max_message_bytes", "extension_queue"):
        if not isinstance(link[name], int) or link[name] <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if link["max_message_bytes"] > 4096:
        raise ValueError("max_message_bytes cannot exceed 4096")
    minimum_response = (2 * 226 * 10 / baud + 2 * 226 * 8 / air_rate
                        + 2 * link["turnaround_seconds"] + 0.05)
    if link["response_timeout"] < max(0.2, minimum_response):
        raise ValueError("response_timeout is too short for the configured UART/air rate")
    tick = link.get("tick_seconds", 0.5)
    if not isinstance(tick, (int, float)) or not math.isfinite(tick) or not 0.02 <= tick <= 1:
        raise ValueError("link.tick_seconds must be 0.02..1 seconds")
    positive(config["geometry"]["quantization_m"], "quantization_m")
    positive(config["foxglove"]["stale_seconds"], "stale_seconds")
    for name in ("cache_markers", "snapshot_bytes"):
        if (
            not isinstance(config["geometry"][name], int)
            or config["geometry"][name] < 1
        ):
            raise ValueError(f"geometry.{name} must be a positive integer")
    if config["geometry"]["snapshot_bytes"] > link["max_message_bytes"]:
        raise ValueError("snapshot_bytes exceeds max_message_bytes")
    if config["geometry"]["snapshot_bytes"] > link["fragment_bytes"] * 64:
        raise ValueError("snapshot_bytes exceeds 64 fragment capacity")
    for key in ("alignment_buffer_seconds", "speed_alignment_tolerance_seconds"):
        value = config["foxglove"].get(
            key, 3.0 if key == "alignment_buffer_seconds" else 0.2
        )
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 60
        ):
            raise ValueError(f"foxglove.{key} must be between 0 and 60 seconds")
    styles = config["foxglove"].get("styles")
    if not isinstance(styles, dict):
        raise ValueError(
            "foxglove.styles is required; upgrade radio.yaml with schema 2"
        )
    for key in ("line_width", "point_size", "arrow_shaft", "arrow_head"):
        positive(styles[key], f"styles.{key}")
    for key in ("cone_size", "arrow_size"):
        if len(styles[key]) != 3:
            raise ValueError(f"styles.{key} must have three sizes")
        for value in styles[key]:
            positive(value, f"styles.{key}")
    for key in ("default", "vehicle", "centerline", "path", "trajectory", "control"):
        rgba = styles["colors"][key]
        if len(rgba) != 4 or any(
            not isinstance(v, int) or not 0 <= v <= 255 for v in rgba
        ):
            raise ValueError(f"styles.colors.{key} must be four bytes")
    cli = config.get("cli", {})
    for key in ("scan_seconds", "retry_seconds", "status_interval"):
        if key in cli:
            positive(cli[key], f"cli.{key}")
    if "chunk_bytes" in cli and (
        not isinstance(cli["chunk_bytes"], int) or not 64 <= cli["chunk_bytes"] <= 2048
    ):
        raise ValueError("cli.chunk_bytes must be 64..2048")
    for key, layer in config["layers"].items():
        positive(layer["interval"], f"{key}.interval")
        positive(layer["weight"], f"{key}.weight")
        if key != "vehicle":
            for name in ("markers", "points"):
                if not isinstance(layer[name], int) or not 2 <= layer[name] <= 256:
                    raise ValueError(f"{key}.{name} must be 2..256")
