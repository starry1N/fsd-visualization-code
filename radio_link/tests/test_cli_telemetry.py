"""CLI producer -> durable radio spool -> disk ACK -> full Foxglove log contracts."""

from copy import deepcopy
import asyncio
import importlib.util
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
import pytest
import websockets
from fsd_radio.cli_telemetry import CliForwarder, CliReceiver, expand_status, stream_id
from fsd_radio.link import Outbox
from fsd_radio.protocol import Packet, MASTER, MAX_WIRE, pack_message, unpack_message
from fsd_radio.foxglove_view import FoxgloveView

ROOT = Path(__file__).resolve().parents[3]


def source_module(name):
    path = ROOT / "fsd_startup" / f"{name}.py"
    spec = importlib.util.spec_from_file_location("_cli_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settings(config, tmp_path):
    c = deepcopy(config)
    c["cli"].update(
        project_root=str(tmp_path / "linux"),
        checkpoint_file=str(tmp_path / "checkpoint.json"),
        telemetry_file=str(tmp_path / "telemetry.json"),
        state_file=str(tmp_path / "state.json"),
        scan_seconds=0.01,
        chunk_bytes=64,
        retry_seconds=1,
        status_interval=10,
    )
    return c


def make_source(tmp_path, texts):
    path = tmp_path / "linux/logs/sessions/20261019_180000_123456/manager.log"
    path.parent.mkdir(parents=True)
    module = source_module("log_context")
    events = [
        module.append_manager_event(
            path,
            text,
            "error" if "Traceback" in text else "info",
            1792395769315376568 + i,
        )
        for i, text in enumerate(texts)
    ]
    return path.with_name("manager.events.jsonl"), events


@pytest.mark.parametrize("size", [1, 64, 512, 2048])
def test_log_block_wire_roundtrip(size):
    msg = {
        "k": "cli_log",
        "stream": "ab" * 16,
        "offset": 1234,
        "t": 1792395769315376568,
        "data": bytes(i % 251 for i in range(size)),
    }
    assert unpack_message(pack_message(msg)) == msg
    ack = {"k": "cli_ack", "stream": msg["stream"], "offset": 1234 + size}
    assert unpack_message(pack_message(ack)) == ack
    frame = Packet(MASTER, 1, 1, count=1, payload=bytes(144)).encode()
    assert len(frame) == MAX_WIRE == 226 and MAX_WIRE < 240


def test_complete_unicode_stacktrace_disk_ack_loss_and_restart(config, tmp_path):
    texts = [
        "[18:00:00] 启动：模块在线\n",
        "[18:00:01] Traceback (most recent call last):\n"
        + ("完整堆栈 中文 αβ\n" * 1500)
        + "RuntimeError: 完整报错结束\n",
        "[18:00:02] 恢复\n",
    ]
    source, events = make_source(tmp_path, texts)
    c = settings(config, tmp_path)
    out = Outbox(c)
    forward = CliForwarder(c, out)
    published = []
    acks = []
    receiver = CliReceiver(tmp_path / "pc", published.append, acks.append)
    forward.tick(0)
    msg = unpack_message(pack_message(forward.pending["message"]))
    receiver.accept(msg)
    # Disk write completed but the first application ACK is lost.
    acks.clear()
    receiver.accept(msg)
    forward.acknowledge(acks.pop())
    receiver = CliReceiver(tmp_path / "pc", published.append, acks.append)
    forward = CliForwarder(c, Outbox(c))
    for step in range(1, 6000):
        forward.tick(step)
        if forward.pending is None:
            break
        message = unpack_message(pack_message(forward.pending["message"]))
        receiver.accept(message)
        forward.acknowledge(acks.pop())
    else:
        pytest.fail("journal transfer did not drain")
    key = stream_id(source)
    assert (tmp_path / "pc" / f"{key}.events.jsonl").read_bytes() == source.read_bytes()
    assert (tmp_path / "pc" / f"{key}.manager.log").read_bytes() == "".join(
        texts
    ).encode("utf-8")
    assert [r["text"] for r in published] == texts
    assert [str(r["t"]) for r in published] == [e["source_stamp_ns"] for e in events]
    assert forward.backlog == 0
    assert not out.latest.get("cli_log")


def test_restart_projects_durable_acknowledged_events_without_new_radio_data(tmp_path):
    source, _ = make_source(tmp_path, ["完整错误\nTraceback: 恢复\n"])
    key = stream_id(source)
    acks = []
    receiver = CliReceiver(tmp_path / "pc", lambda event: None, acks.append)
    # Simulate interruption after the disk ACK but before the display projection.
    receiver.publish_records = lambda *args: None
    receiver.accept({"k": "cli_log", "stream": key, "offset": 0,
                     "t": 1, "data": source.read_bytes()})
    assert acks.pop()["offset"] == source.stat().st_size
    published = []
    CliReceiver(tmp_path / "pc", published.append, acks.append)
    assert [event["text"] for event in published] == ["完整错误\nTraceback: 恢复\n"]
    assert (tmp_path / "pc" / f"{key}.manager.log").read_text(encoding="utf-8") == published[0]["text"]
    # Another restart preserves the projection and does not duplicate its records.
    CliReceiver(tmp_path / "pc", published.append, acks.append)
    assert len(published) == 1 and not acks


def test_gap_rewind_conflict_and_no_ack_on_disk_failure(config, tmp_path, monkeypatch):
    received = []
    acks = []
    receiver = CliReceiver(tmp_path / "pc", received.append, acks.append)
    msg = {"k": "cli_log", "stream": "ab" * 16, "offset": 100, "t": 1, "data": b"part"}
    receiver.accept(msg)
    assert acks.pop()["offset"] == 0 and not received
    receiver.accept({**msg, "offset": 0})
    assert acks.pop()["offset"] == 4
    with pytest.raises(ValueError, match="conflicting"):
        receiver.accept({**msg, "offset": 0, "data": b"xxxx"})
    import fsd_radio.cli_telemetry as module

    monkeypatch.setattr(
        module.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk failure"))
    )
    with pytest.raises(OSError):
        receiver.accept({**msg, "offset": 4})
    assert not acks


def test_archive_relocation_and_pc_rewind(config, tmp_path):
    source, _ = make_source(tmp_path, ["第一条\n", "第二条\n"])
    c = settings(config, tmp_path)
    f = CliForwarder(c, Outbox(c))
    f.tick(0)
    old_id = stream_id(source)
    target = source.parents[2] / "skidpad" / source.parent.name
    target.parent.mkdir()
    source.parent.rename(target)
    f.scan()
    assert old_id in f.files
    f.acknowledge({"k": "cli_ack", "stream": old_id, "offset": 0})
    f.tick(1)
    assert f.pending["message"]["offset"] == 0


def test_cli_sensor_capture_and_status_preserve_detail_and_times(config, tmp_path):
    module = source_module("dashboard")
    telemetry = module.Telemetry.__new__(module.Telemetry)
    telemetry.lock = threading.Lock()
    telemetry.samples = {}
    telemetry.error = ""
    msg = SimpleNamespace(
        header=SimpleNamespace(
            frame_id="lidar", stamp=SimpleNamespace(sec=12, nanosec=345)
        ),
        width=4,
        height=5,
        data=b"x" * 9,
    )
    telemetry.record("雷达", msg)
    rows = telemetry.snapshot(["GNSS", "雷达"])
    assert rows[0] == {"name": "GNSS", "received": False}
    assert rows[1]["source_stamp_ns"] == "12000000345"
    assert "点数=20" in rows[1]["detail"] and rows[1]["time_origin"] == "header"
    c = settings(config, tmp_path)
    Path(c["cli"]["telemetry_file"]).write_text(
        json.dumps(
            {
                "source_stamp_ns": "1792395769315376568",
                "sensors_data": rows,
                "vehicle_data": [],
            }
        ),
        encoding="utf-8",
    )
    out = Outbox(c)
    f = CliForwarder(c, out)
    f.tick(0)
    decoded = unpack_message(out.latest["cli_status"])
    expanded = expand_status(decoded)
    assert expanded["sensors"][1]["name"] == "雷达"
    assert expanded["sensors"][1]["source_stamp_ns"] == "12000000345"
    assert expanded["sensors"][1]["detail"] == rows[1]["detail"]
    assert expanded["sensors"][0]["received"] is False
    assert f.status_error is None


def test_prioritized_downlink_and_one_shot_auxiliary(config):
    out = Outbox(config)
    out.update_aux(
        "cli_log",
        {"k": "cli_log", "stream": "ab" * 16, "offset": 0, "t": 1, "data": b"log"},
        one_shot=True,
        weight=6,
    )
    out.update_aux(
        "cli_ack",
        {"k": "cli_ack", "stream": "ab" * 16, "offset": 3},
        one_shot=True,
        weight=100,
    )
    first = out.next_piece(0)
    assert first.key == "cli_ack"
    out.finish_piece(first, True)
    assert not out.aux_pending("cli_ack")
    second = out.next_piece(1)
    assert second.key == "cli_log"
    out.finish_piece(second, True)
    assert not out.aux_pending("cli_log")


def test_foxglove_real_log_channel_is_full_source_text(config):
    c = deepcopy(config)
    c["foxglove"].update(port=0, alignment_buffer_seconds=0)
    view = FoxgloveView(c, lambda body: None)
    stamp = 1792395769315376568
    text = "Traceback:\n" + ("保留完整日志内容\n" * 200) + "错误结束\n"

    async def client():
        async with websockets.connect(
            f"ws://127.0.0.1:{view.server.port}",
            subprotocols=["foxglove.sdk.v1", "foxglove.websocket.v1"],
        ) as ws:
            ident = None
            while True:
                frame = await asyncio.wait_for(ws.recv(), 3)
                if isinstance(frame, str):
                    obj = json.loads(frame)
                    if obj.get("op") == "advertise":
                        channel = next(
                            (
                                ch
                                for ch in obj["channels"]
                                if ch["topic"] == "/radio/cli/log"
                            ),
                            None,
                        )
                        if channel:
                            assert channel["schemaName"] == "foxglove.Log"
                            ident = channel["id"]
                            await ws.send(
                                json.dumps(
                                    {
                                        "op": "subscribe",
                                        "subscriptions": [
                                            {"id": ident, "channelId": ident}
                                        ],
                                    }
                                )
                            )
                            await asyncio.sleep(0.05)
                            view.accept(
                                {
                                    "k": "cli_event",
                                    "t": stamp,
                                    "text": text,
                                    "level": "error",
                                    "stream": "ab" * 16,
                                    "offset": 0,
                                }
                            )
                elif frame[0] == 1 and struct.unpack_from("<I", frame, 1)[0] == ident:
                    assert struct.unpack_from("<Q", frame, 5)[0] == stamp
                    from foxglove import messages as m

                    expected = m.Log(
                        timestamp=m.Timestamp(sec=stamp // 10**9, nsec=stamp % 10**9),
                        level=m.LogLevel.Error,
                        message=text,
                        name="CLI:" + "ab" * 16,
                        file="0",
                    )
                    assert frame[13:] == expected.encode()
                    break

    try:
        asyncio.run(client())
    finally:
        view.close()


def test_endpoints_carry_journal_disk_ack_and_pc_command(config, tmp_path):
    from queue import Queue, Empty
    from test_link import pair

    source, _ = make_source(tmp_path, ["完整错误\n" + ("Traceback 中文\n" * 30)])
    c = settings(config, tmp_path)
    c["link"].update(
        bytes_per_second=10000, response_timeout=0.08, turnaround_seconds=0.002
    )
    down, up = Queue(), Queue()
    master, pc = pair(c, up.put, down.put)
    forward = CliForwarder(c, master.outbox)
    events = []
    receiver = CliReceiver(
        tmp_path / "pc",
        events.append,
        lambda msg: pc.outbox.update_aux("cli_ack", msg, one_shot=True, weight=100),
    )
    pc.outbox.extension({"type": "ping", "id": "small-upstream-command"})
    pc.transport.drop = 1
    stop = threading.Event()
    threads = [threading.Thread(target=e.run, args=(stop,)) for e in (master, pc)]
    commands = []
    try:
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            forward.tick()
            try:
                while True:
                    msg = down.get_nowait()
                    if msg["k"] == "cli_log":
                        receiver.accept(msg)
            except Empty:
                pass
            try:
                while True:
                    msg = up.get_nowait()
                    if msg["k"] == "cli_ack":
                        forward.acknowledge(msg)
                    else:
                        commands.append(msg)
            except Empty:
                pass
            if events and forward.backlog == 0 and commands:
                break
            time.sleep(0.001)
        assert events and forward.backlog == 0
        assert commands[0]["body"]["id"] == "small-upstream-command"
        assert (
            tmp_path / "pc" / (stream_id(source) + ".events.jsonl")
        ).read_bytes() == source.read_bytes()
    finally:
        stop.set()
        for thread in threads:
            thread.join(2)


def test_real_manager_emit_has_exact_timestamped_journal_projection(tmp_path):
    import ast
    from datetime import datetime
    from queue import Queue

    module = source_module("log_context")
    tree = ast.parse((ROOT / "fsd_startup/manager.py").read_text(encoding="utf-8"))
    method = next(
        node
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == "Manager"
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "emit"
    )
    namespace = {
        "time": time,
        "datetime": datetime,
        "append_manager_event": module.append_manager_event,
        "COLORS": {},
    }
    exec(
        compile(ast.Module(body=[method], type_ignores=[]), "manager.emit", "exec"),
        namespace,
    )
    path = tmp_path / "20261019_180000_123456/manager.log"
    path.parent.mkdir()
    with path.open("w", encoding="utf-8") as output:
        instance = SimpleNamespace(
            log=output,
            log_path=path,
            log_lock=threading.RLock(),
            silent=True,
            color=False,
            ui_events=Queue(),
        )
        namespace["emit"](
            instance, "失败：完整堆栈\nTraceback:\nUnicode错误 αβ", "error"
        )
    records = [
        json.loads(line)
        for line in path.with_name("manager.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[0]["text"] == path.read_text(encoding="utf-8")
    assert records[0]["level"] == "error" and int(records[0]["source_stamp_ns"]) > 0


def test_stream_identity_survives_rename_and_same_directory_name_is_not_reused(tmp_path):
    module=source_module('log_context')
    first=tmp_path/'one/same/manager.log';second=tmp_path/'two/same/manager.log'
    first.parent.mkdir(parents=True);second.parent.mkdir(parents=True)
    e1=module.append_manager_event(first,'one\n','info',1)
    e2=module.append_manager_event(second,'two\n','info',2)
    assert e1['stream_id']!=e2['stream_id']
    module.append_manager_event(first,'more\n','info',3)
    assert stream_id(first.with_name('manager.events.jsonl'))==e1['stream_id']
