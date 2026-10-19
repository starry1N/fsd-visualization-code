from copy import deepcopy
import math
import zlib
import pytest
from fsd_radio.essentials import (
    ARROW,
    BOX,
    CONES,
    STRIP,
    SEGMENTS,
    POINTS,
    CYLINDER,
    ELLIPSOID,
    decode,
    encode,
    expanded_records,
    profile_tag,
    rgb_code,
    rgb_color,
)
from fsd_radio.geometry import MarkerCache, essential_marker, scene_snapshot
from fsd_radio.protocol import pack_message, unpack_message
from test_geometry import marker

LOOKUP = lambda *args: ((0, 0, 0), (0, 0, 0, 1))


def scene(config, records, layer="path"):
    records = [{"t": 1792248147046000000, **r} for r in records]
    return {
        "e": 3,
        "ep": 0,
        "k": "scene",
        "l": layer,
        "t": 1792248147046000000,
        "a": 0.2,
        "g": profile_tag(config),
        "o": records,
    }


def test_binary_path_has_no_style_or_namespace_and_preserves_geometry(config):
    m = marker(
        ns="very_long_namespace",
        text="remove label",
        scale=(0.3, 0.5, 0.7),
        points=[(0, 0, 0), (1, -1, 0), (2, 3, -0.5)],
    )
    record = essential_marker("path", m, LOOKUP, 0.05, 48)
    assert set(record) == {"h", "p", "t"}
    assert record["p"] == [[20, 0, 0], [40, -20, 0], [60, 60, -10]]
    message = scene(config, [record])
    data = pack_message(message)
    assert unpack_message(data) == message
    assert len(data) < 70
    expanded = expanded_records(message, config)
    assert expanded[0][5] == config["foxglove"]["styles"]["colors"]["path"]
    assert expanded[0][6] == record["p"]


@pytest.mark.parametrize(
    "kind", [CONES, STRIP, SEGMENTS, POINTS, BOX, ELLIPSOID, CYLINDER, ARROW]
)
def test_all_essential_shapes_roundtrip_and_sdk(config, kind):
    from foxglove import messages as m
    from foxglove.channels import SceneUpdateChannel
    from fsd_radio.foxglove_view import entity_from_record
    import foxglove

    points = [[1, -2, 0], [20, 30, -4]]
    record = {"h": kind, "p": points, "c": [rgb_code([0, 255, 255, 255])]}
    if kind in (BOX, ELLIPSOID, CYLINDER):
        record.update(d=[20, 10, 8], r=[0, 0, 23170, 23170])
    message = scene(config, [record], layer="guided")
    decoded = unpack_message(pack_message(message))
    assert decoded == message
    channel = SceneUpdateChannel("/essential-test", context=foxglove.Context())
    records = expanded_records(decoded, config)
    for record in records:
        channel.log(
            m.SceneUpdate(
                entities=[
                    entity_from_record(
                        record, "map", 0.05, 1_000_000_000, 10, message["t"]
                    )
                ]
            )
        )


def test_cones_only_keep_centers_and_color_code(config):
    record = essential_marker(
        "map", marker(type=3, points=[], scale=(0.2, 0.2, 0.5)), LOOKUP, 0.05, 12
    )
    assert set(record) == {"h", "p", "c", "t"} and record["h"] == CONES
    assert record["c"] == [224]  # RGB332 red; no invented semantic class.
    assert rgb_color(record["c"][0]) == [255, 0, 0, 255]
    assert (
        essential_marker("map", marker(type=9, text="dynamic label"), LOOKUP, 0.05, 12)
        is None
    )


def test_boxes_keep_size_and_orientation_arrows_keep_endpoints():
    box = essential_marker(
        "guided",
        marker(
            type=1,
            points=[],
            scale=(2, 1, 0.5),
            orientation=(0, 0, math.sqrt(0.5), math.sqrt(0.5)),
        ),
        LOOKUP,
        0.05,
        12,
    )
    assert box["d"] == [40, 20, 10] and box["r"][2] > 23000
    arrow = essential_marker(
        "control",
        marker(type=0, points=[], position=(1, 2, 0), scale=(3, 0.1, 0.2)),
        LOOKUP,
        0.05,
        12,
    )
    assert arrow == {"t": 1, "h": ARROW, "p": [[20, 40, 0], [80, 40, 0]]}


def test_vehicle_one_fragment_and_timestamp_precision(config):
    msg = {
        "e": 2,
        "k": "vehicle",
        "t": 1792248147046376568,
        "a": 1.2,
        "g": profile_tag(config),
        "p": [8, -96, -47],
        "r": [0.0434, -0.051, 0.748, 0.6603],
        "v": 0.09,
    }
    data = encode(msg)
    result = decode(data)
    assert len(data) < 32
    assert result["p"] == msg["p"] and result["v"] == 0.09 and result["a"] == 1.2
    assert result["t"] == 1792248147046000000
    assert result["r"] == pytest.approx(msg["r"], abs=1e-4)
    assert decode(encode({**msg, "v": None}))["v"] is None


def test_profile_mismatch_rejected_but_local_style_can_change(config):
    message = scene(config, [{"h": STRIP, "p": [[0, 0, 0], [20, 0, 0]]}])
    other = deepcopy(config)
    other["fixed_frame"] = "odom"
    with pytest.raises(ValueError, match="frame or quantization"):
        expanded_records(message, other)
    other = deepcopy(config)
    other["foxglove"]["styles"]["colors"]["path"] = [255, 0, 0, 255]
    assert profile_tag(config) == profile_tag(other)
    assert expanded_records(message, other)[0][5] == [255, 0, 0, 255]


def test_atomic_empty_snapshot_and_local_only_diagnostics(config):
    cache = MarkerCache()
    cache.apply([marker(type=3, points=[])], 1)
    diagnostics = {}
    message = scene_snapshot("map", cache, LOOKUP, config, 1, diagnostics=diagnostics)
    assert diagnostics["source_markers"] == 1 and "s" not in message
    cache.apply([marker(action=3)], 2)
    empty = scene_snapshot("map", cache, LOOKUP, config, 2)
    assert decode(encode(empty))["o"] == []


def test_bad_binary_frames_are_bounded(config):
    data = encode(scene(config, []))
    assert data[:1] == b"S"
    for invalid in (
        data[:-1],
        data + b"junk",
        b"z" + zlib.compress(b"S" * 10000),
        b"zgarbage",
        b"S\x09" + data[2:],
    ):
        with pytest.raises(ValueError):
            decode(invalid)
    with pytest.raises(ValueError):
        encode(scene(config, [{"h": SEGMENTS, "p": [[1, 2, 3]]}]))
    with pytest.raises(ValueError):
        encode(scene(config, [{"h": BOX, "p": [[1, 2, 3]]}]))
    with pytest.raises(ValueError):
        encode(scene(config, [{"h": POINTS, "p": [[2**31, 0, 0]]}]))


def test_per_point_source_colors_and_delta_coordinates(config):
    record = essential_marker(
        "guided",
        marker(
            type=8,
            points=[(0, 0, 0), (1, 1, 0)],
            colors=[[255, 0, 0, 255], [0, 255, 0, 255]],
        ),
        LOOKUP,
        0.05,
        12,
    )
    decoded = decode(encode(scene(config, [record], layer="guided")))
    assert decoded["o"][0]["c"] == [224, 28]
    assert decoded["o"][0]["p"] == [[20, 0, 0], [40, 20, 0]]


def test_reproducible_radio_cost_reduction(config):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "radio_bandwidth",
        Path(__file__).resolve().parents[1] / "tools/compare_bandwidth.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for row in module.measure(config):
        assert (
            row["after"]["two_way_uart_bytes"]
            < row["before"]["two_way_uart_bytes"] * 0.6
        )
    for _, _, message in module.cases(config):
        if message["k"] == "scene":
            decoded = unpack_message(pack_message(message))
            assert sum(len(r["p"]) for r in decoded["o"]) == sum(
                len(r["p"]) for r in message["o"]
            )
