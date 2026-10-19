"""Source-time contracts: binary fidelity, reorder, independent speed and SDK wire time."""

from copy import deepcopy
import asyncio
import json
import struct
import time
from types import SimpleNamespace
import pytest
import websockets
from fsd_radio.essentials import encode, decode, profile_tag
from fsd_radio.foxglove_view import FoxgloveView, entity_from_record
from fsd_radio.geometry import MarkerCache, scene_snapshot
from fsd_radio.link import Outbox
from fsd_radio.protocol import unpack_message
from test_geometry import marker

T = 1792395769315376568


def vehicle(config, stamp=T, speed_stamp=None, **extra):
    return {
        "e": 3,
        "ep": 1,
        "k": "vehicle",
        "g": profile_tag(config),
        "t": stamp,
        "vt": stamp if speed_stamp is None else speed_stamp,
        "p": [0, 0, 0],
        "r": [0, 0, 0, 1],
        "v": 1.2,
        "a": 0.0,
        **extra,
    }


@pytest.mark.parametrize("delta", [-3_456_789_012, 0, 123_456_789])
def test_nanoseconds_and_independent_speed_roundtrip(config, delta):
    msg = vehicle(config, speed_stamp=T + delta)
    assert decode(encode(msg)) == msg


def test_mixed_marker_times_survive_cache_encoding_and_tf(config):
    cache = MarkerCache()
    sources = [
        marker(id=1, stamp=T, frame_locked=True),
        marker(id=2, stamp=T - 123456789),
    ]
    cache.apply(sources, T)
    assert len(cache.live(T)) == 2
    queried = []

    def lookup(frame, stamp):
        queried.append(stamp)
        return (0, 0, 0), (0, 0, 0, 1)

    msg = scene_snapshot("path", cache, lookup, config, T)
    result = decode(encode(msg))
    assert result["t"] == T
    assert [r["t"] for r in result["o"]] == [T, T - 123456789]
    assert queried == [T, T - 123456789]


class Capture:
    def __init__(self):
        self.rows = []

    def log(self, msg, *, log_time=None):
        self.rows.append((log_time, msg))


@pytest.fixture
def view(config):
    c = deepcopy(config)
    c["foxglove"]["port"] = 0
    v = FoxgloveView(c, lambda body: None)
    v.channels = {key: Capture() for key in v.channels}
    v.json_channels = {key: Capture() for key in v.json_channels}
    yield v
    v.close()


def test_reorder_buffer_no_future_speed_substitution_and_no_fake_refresh(
    view, config, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr("fsd_radio.foxglove_view.time.monotonic", lambda: clock[0])
    view.accept(vehicle(config, T + 10**9, T + 2 * 10**9))
    clock[0] = 101.0
    view.accept(vehicle(config, T, T - 100_000_000))
    assert not view.json_channels["state"].rows
    view.flush(103.0)
    state = view.json_channels["state"].rows
    assert [t for t, _ in state] == [T, T + 10**9]
    assert state[0][1]["speed_m_s"] == 1.2
    assert state[1][1]["speed_m_s"] is None
    assert [t for t, _ in view.json_channels["speed"].rows] == [
        T - 100_000_000,
        T + 2 * 10**9,
    ]
    view.flush(104.0)
    assert view.json_channels["speed"].rows[-1][0] == T + 2 * 10**9
    assert state[0][1]["timestamp"] == {"sec": T // 10**9, "nsec": T % 10**9}
    assert state[0][1]["source_stamp_ns"] == str(T)
    count = len(state)
    clock[0] = 105.0
    view.refresh()
    assert (
        len(state) == count
    )  # Diagnostics do not repeat source data with current time.
    view.accept(vehicle(config, T - 10**9))
    view.flush(110.0)
    assert len(state) == count and view.timing["late_drops"] >= 1


def test_unknown_time_is_not_replaced_by_receipt_time(view, config):
    view.accept(vehicle(config, 0, 0))
    view.flush(time.monotonic() + 5)
    assert not view.json_channels["state"].rows
    assert not view.json_channels["speed"].rows
    assert view.timing["unknown_source_times"] == 2


def test_epoch_change_clears_pending_and_retires_old_clock(view, config):
    view.accept(vehicle(config))
    view.accept(vehicle(config, 123456789, ep=2))
    assert view.clock_epoch == 2
    assert all(e[1] < T for e in view.pending)
    view.accept(vehicle(config, ep=1))
    assert view.clock_epoch == 2 and view.timing["late_drops"] == 1


def test_late_subscriber_replay_preserves_source_time(view, config):
    view.buffer_seconds = 0
    view.accept(vehicle(config))
    before = len(view.json_channels["state"].rows)
    view.listener.on_subscribe(None, SimpleNamespace(topic="/radio/state"))
    view.refresh()
    assert len(view.json_channels["state"].rows) == before + 1
    assert view.json_channels["state"].rows[-1][0] == T


def test_extension_time_is_transmitted_not_inferred_on_receive(config, view):
    out = Outbox(config)
    out.extension({"type": "sample"}, T, "ros")
    msg = unpack_message(out.extensions[0])
    assert msg["t"] == T and msg["clock_domain"] == "ros"
    view.buffer_seconds = 0
    view.accept(msg)
    t, payload = view.json_channels["extension/rx"].rows[-1]
    assert t == T and payload["source_stamp_ns"] == str(T)
    with pytest.raises(ValueError):
        out.extension({"source_stamp_ns": -1})


def test_scene_entity_uses_original_object_stamp():
    record = [
        "test",
        0,
        [0, 0, 0],
        [0, 0, 0, 1],
        [1, 0.1, 0.2],
        [255, 0, 0, 255],
        [],
        [],
        "",
    ]
    actual = entity_from_record(record, "map", 0.05, T + 10**9, 10, T)
    from foxglove import messages as m

    expected = m.SceneEntity(
        timestamp=m.Timestamp(sec=T // 10**9, nsec=T % 10**9),
        frame_id="map",
        id="test",
        lifetime=m.Duration(sec=10),
        metadata=[
            m.KeyValuePair(key="source_ros_stamp_ns", value=str(T)),
            m.KeyValuePair(key="display", value="quantized low-bandwidth sample"),
        ],
        arrows=[
            m.ArrowPrimitive(
                pose=m.Pose(position=m.Vector3(), orientation=m.Quaternion(w=1)),
                shaft_length=0.77,
                shaft_diameter=0.1,
                head_length=0.23,
                head_diameter=0.2,
                color=m.Color(r=1, a=1),
            )
        ],
    )
    assert actual.encode() == expected.encode()


def test_sdk_websocket_timestamp_is_source_time(config):
    c = deepcopy(config)
    c["foxglove"].update(port=0, alignment_buffer_seconds=0)
    v = FoxgloveView(c, lambda body: None)

    async def client():
        async with websockets.connect(
            f"ws://127.0.0.1:{v.server.port}",
            subprotocols=["foxglove.sdk.v1", "foxglove.websocket.v1"],
        ) as ws:
            topics = {}
            subscribed = False
            observed = {}
            while len(observed) < 2:
                frame = await asyncio.wait_for(ws.recv(), 3)
                if isinstance(frame, str):
                    o = json.loads(frame)
                    if o.get("op") == "advertise":
                        topics.update(
                            {
                                ch["id"]: ch["topic"]
                                for ch in o["channels"]
                                if ch["topic"] in ("/radio/state", "/radio/speed")
                            }
                        )
                        if len(topics) == 2 and not subscribed:
                            await ws.send(
                                json.dumps(
                                    {
                                        "op": "subscribe",
                                        "subscriptions": [
                                            {"id": i, "channelId": i} for i in topics
                                        ],
                                    }
                                )
                            )
                            await asyncio.sleep(0.05)
                            v.accept(vehicle(config, speed_stamp=T - 123456789))
                            subscribed = True
                elif frame[0] == 1:
                    ident, stamp = struct.unpack_from("<IQ", frame, 1)
                    observed[topics[ident]] = (stamp, json.loads(frame[13:]))
            assert observed["/radio/state"][0] == T
            assert observed["/radio/speed"][0] == T - 123456789
            assert observed["/radio/speed"][1]["speed_m_s"] == 1.2

    try:
        asyncio.run(client())
    finally:
        v.close()


@pytest.mark.parametrize("data", [b"S", b"V", b"S\x03", b"V\x03"])
def test_truncated_timed_header_is_a_value_error(data):
    with pytest.raises(ValueError):
        decode(data)


def test_missing_or_invalid_source_fields_are_rejected(config):
    msg = vehicle(config)
    del msg["vt"]
    with pytest.raises(KeyError):
        encode(msg)
    for bad in (-1, 2**63, 1.5, True):
        with pytest.raises(ValueError):
            encode(vehicle(config, bad))
    with pytest.raises(ValueError):
        encode(vehicle(config, ep=-1))
