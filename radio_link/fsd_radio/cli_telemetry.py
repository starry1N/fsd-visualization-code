"""Durable essential CLI event transfer and snapshots of the existing CLI collector.

The selected manager journal is the source spool. Offsets advance after a PC disk ACK.
No ROS or Foxglove import here; serial I/O remains owned by RadioWorker.
"""

import re
import json
import logging
import os
from pathlib import Path
import struct
import time
import zlib
from collections import deque

LOG = logging.getLogger(__name__)
LOG_HEADER = struct.Struct(">B16sQQH")
ACK_HEADER = struct.Struct(">B16sQ")
SENSORS = ("GNSS", "惯导", "雷达", "相机", "融合")


def stream_id(path):
    with Path(path).open("rb") as source:
        match = re.match(rb'^\{"stream_id":"([0-9a-f]{32})"', source.read(96))
    if match is None:
        raise ValueError("Invalid CLI journal identity; preserve the source file")
    return match[1].decode("ascii")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def encode_payload(message, limit=4096):
    identity = bytes.fromhex(message["stream"])
    if len(identity) != 16:
        raise ValueError("invalid CLI stream id")
    offset = message["offset"]
    if not isinstance(offset, int) or not 0 <= offset < 2**63:
        raise ValueError("invalid CLI offset")
    if message["k"] == "cli_ack":
        return b"A" + ACK_HEADER.pack(1, identity, offset)
    data = message["data"]
    stamp = message["t"]
    if not isinstance(data, bytes) or not 1 <= len(data) <= 2048:
        raise ValueError("invalid CLI data block")
    if not isinstance(stamp, int) or not 0 <= stamp < 2**63:
        raise ValueError("invalid CLI capture time")
    raw = b"L" + LOG_HEADER.pack(1, identity, offset, stamp, len(data)) + data
    if len(raw) > limit:
        raise ValueError("CLI block exceeds message limit")
    compressed = b"b" + zlib.compress(raw, 6)
    return compressed if len(compressed) < len(raw) else raw


def decode_payload(data, limit=4096):
    if data[:1] == b"b":
        d = zlib.decompressobj()
        try:
            data = d.decompress(data[1:], limit + 1)
        except zlib.error as exc:
            raise ValueError("invalid CLI compression") from exc
        if len(data) > limit or not d.eof or d.unused_data or d.unconsumed_tail:
            raise ValueError("invalid CLI compressed length")
    if len(data) > limit:
        raise ValueError("CLI block exceeds message limit")
    if data[:1] == b"A" and len(data) == ACK_HEADER.size + 1:
        version, identity, offset = ACK_HEADER.unpack(data[1:])
        result = {"k": "cli_ack", "stream": identity.hex(), "offset": offset}
    elif data[:1] == b"L" and len(data) >= LOG_HEADER.size + 1:
        version, identity, offset, stamp, count = LOG_HEADER.unpack(
            data[1 : 1 + LOG_HEADER.size]
        )
        payload = data[1 + LOG_HEADER.size :]
        if len(payload) != count or not 1 <= count <= 2048 or stamp >= 2**63:
            raise ValueError("invalid CLI block length or time")
        result = {
            "k": "cli_log",
            "stream": identity.hex(),
            "offset": offset,
            "t": stamp,
            "data": payload,
        }
    else:
        raise ValueError("invalid CLI encoding")
    if version != 1 or offset >= 2**63:
        raise ValueError("invalid CLI version or offset")
    return result


class CliForwarder:
    def __init__(self, config, outbox):
        self.options = config["cli"]
        self.root = Path(self.options["project_root"])
        self.selection_file = Path(self.options["selection_file"])
        self.checkpoint = Path(self.options["checkpoint_file"])
        self.outbox = outbox
        self.cursors = {}
        if self.checkpoint.exists():
            self.cursors = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        if not isinstance(self.cursors, dict) or any(
            not isinstance(v, int) or v < 0 for v in self.cursors.values()
        ):
            raise ValueError(
                "invalid CLI sender checkpoint; preserve it and repair explicitly"
            )
        self.files = {}
        self.pending = None
        self.last_scan = -1e9
        self.last_status = -1e9
        self.error = None
        self.status_error = None
        self.source_warning = None
        self.backlog = 0
        self.current_scope = None
        self.run_message = None

    def scan(self):
        # The CLI/log owner selects one run. Never search or drain historical roots.
        try:
            selected = json.loads(self.selection_file.read_text(encoding="utf-8"))
            scope = selected["scope"]
            stamp = int(selected["t"])
            if (not isinstance(scope, str) or len(scope) != 32
                    or any(c not in "0123456789abcdef" for c in scope)
                    or not 0 < stamp < 2**63):
                raise ValueError("invalid current-run selection")
            if scope != self.current_scope:
                self.pending = None
                self.files = {}
                self.outbox.remove_aux("cli_log")
                self.outbox.remove_aux("cli_run")
                self.run_message = None
                self.current_scope = scope
            journal = selected.get("journal")
            files, backlog, key = {}, 0, None
            if journal:
                path = Path(journal)
                if path.name != "manager.radio.events.jsonl":
                    raise ValueError("invalid selected CLI journal")
                # It is normal for a newly selected run to have no events yet.
                try:
                    size = path.stat().st_size
                    if size:
                        key = stream_id(path)
                        files[key] = path
                        backlog = max(0, size - self.cursors.get(key, 0))
                except FileNotFoundError:
                    pass
            self.files, self.backlog = files, backlog
            message = {"k": "cli_run", "scope": scope, "t": stamp,
                       "stream": key, "label": str(selected.get("label", ""))[:128],
                       "active": bool(selected.get("active", False))}
            if message != self.run_message:
                self.outbox.remove_aux("cli_run")
                self.outbox.update_aux("cli_run", message, interval=2.0, weight=10)
                self.run_message = message
            self.source_warning = (
                "Current run journal unavailable; pending block retained"
                if self.pending is not None and self.pending["message"]["stream"] not in files
                else None
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.source_warning = f"Current CLI run unavailable; restart updated CLI: {exc}"
            self.pending = None
            self.files = {}
            self.backlog = 0
            self.outbox.remove_aux("cli_log")
            self.outbox.remove_aux("cli_run")
            self.run_message = None

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        try:
            if now - self.last_scan >= self.options["scan_seconds"]:
                self.scan()
                self.last_scan = now
            if self.pending is not None:
                if now - self.pending["sent_at"] >= self.options[
                    "retry_seconds"
                ] and not self.outbox.aux_pending("cli_log"):
                    self.outbox.update_aux(
                        "cli_log", self.pending["message"], one_shot=True, weight=6
                    )
                    self.pending["sent_at"] = now
            else:
                for key, path in self.files.items():
                    offset = self.cursors.get(key, 0)
                    with path.open("rb") as source:
                        if path.stat().st_size < offset:
                            raise ValueError(
                                "CLI journal truncated; preserve checkpoint and source: "
                                + str(path)
                            )
                        source.seek(offset)
                        data = source.read(self.options["chunk_bytes"])
                    if not data:
                        continue
                    message = {
                        "k": "cli_log",
                        "stream": key,
                        "offset": offset,
                        "t": time.time_ns(),
                        "data": data,
                    }
                    self.pending = {"message": message, "sent_at": now}
                    self.outbox.update_aux("cli_log", message, one_shot=True, weight=6)
                    break
            self.error = None
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        if now - self.last_status >= self.options["status_interval"]:
            self.last_status = now
            try:
                snapshot = Path(self.options["telemetry_file"])
                value = json.loads(snapshot.read_text(encoding="utf-8"))
                stamp = int(value["source_stamp_ns"])
                if stamp <= 0:
                    raise ValueError(
                        "CLI collector has no valid snapshot time; upgrade CLI"
                    )
                # Names and repeated JSON keys are restored locally on the PC.
                rows = []
                for row in value["sensors_data"]:
                    rows.append(
                        [
                            SENSORS.index(row["name"]),
                            row.get("received", False),
                            round(row.get("age_s", 0) * 10),
                            round(row.get("hz", 0) * 10),
                            row.get("source_stamp_ns", "0"),
                            row.get("received_unix_ns", "0"),
                            row.get("detail", ""),
                        ]
                    )
                message = {
                    "k": "cli_status",
                    "v": 1,
                    "t": stamp,
                    "s": rows,
                    "vehicle": value.get("vehicle_data", []),
                    "backlog": self.backlog,
                    "collector_error": value.get("collector_error", ""),
                    "sender_error": self.error,
                    "source_warning": self.source_warning,
                    "current_run": self.run_message,
                    "source_age_s": max(0, round((time.time_ns() - stamp) / 1e9, 1)),
                    "collector_stale": time.time_ns() - stamp > 3_000_000_000,
                }
                self.outbox.update_aux(
                    "cli_status",
                    message,
                    interval=self.options["status_interval"],
                    weight=2,
                )
                self.status_error = None
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.status_error = str(exc)

    def acknowledge(self, message):
        if (
            self.pending is None
            or message["stream"] != self.pending["message"]["stream"]
        ):
            return
        key = message["stream"]
        offset = message["offset"]
        path = self.files.get(key)
        if path is None or not path.is_file():
            # An ACK can arrive before the periodic scan notices the archive move.
            self.scan()
            if self.pending is None or self.pending['message']['stream'] != key:
                return  # The CLI selected another run while this ACK was in flight.
            path = self.files.get(key)
        if path is None or offset > path.stat().st_size:
            raise ValueError("CLI ACK outside source journal")
        # A PC with a restored/empty disk may request replay from an earlier offset.
        cursors = {**self.cursors, key: offset}
        atomic_json(self.checkpoint, cursors)
        self.cursors = cursors
        self.pending = None
        self.outbox.remove_aux("cli_log")
        self.scan()

    def status(self):
        return {
            "journal_files": len(self.files),
            "backlog_bytes": self.backlog,
            "waiting_disk_ack": self.pending is not None,
            "error": self.error,
            "status_error": self.status_error,
            "source_warning": self.source_warning,
            "current_run": self.run_message,
            "scope": "current_run_only",
        }


class CliReceiver:
    def __init__(self, directory, publish, acknowledge, *, current_only=False):
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.publish = publish
        self.acknowledge = acknowledge
        self.error = None
        self.max_record_bytes = 16 * 1024 * 1024
        self.current_only = current_only
        self.active_scope = None
        self.active_stream = None
        self.replay_pending_stream = None
        self.retired_scopes = deque(maxlen=128)
        history = self.directory / 'run_selection.json'
        if current_only and history.exists():
            try:
                previous = json.loads(history.read_text(encoding='utf-8'))
                self.retired_scopes.extend(previous['retired'][-128:])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.error = str(exc)
        if current_only:
            return  # Only the source-selected run may be replayed after restart.
        # Recover records durably ACKed before a previous process stopped projecting them.
        for journal in sorted(self.directory.glob("*.events.jsonl")):
            key = journal.name.removesuffix(".events.jsonl")
            if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
                continue
            try:
                self.publish_records(key, journal)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.error = str(exc)
                LOG.warning("CLI projection recovery failed for %s: %s", key, exc)

    def select_run(self, message):
        scope, stream = message['scope'], message.get('stream')
        for value in (scope, stream):
            if value is not None and (not isinstance(value, str) or len(value) != 32
                                     or any(c not in '0123456789abcdef' for c in value)):
                raise ValueError('invalid CLI run identity')
        if scope is None or not isinstance(message.get('t'), int) or message['t'] <= 0:
            raise ValueError('invalid CLI run selection time')
        if scope in self.retired_scopes:
            return False
        changed = scope != self.active_scope
        previous_stream = self.active_stream
        if changed:
            if self.active_scope is not None:
                self.retired_scopes.append(self.active_scope)
            self.active_scope, self.active_stream = scope, stream
            self.replay_pending_stream = stream
        elif stream is not None:
            if self.active_stream not in (None, stream):
                raise ValueError('CLI stream changed within the same run')
            self.active_stream = stream
            if previous_stream is None:
                self.replay_pending_stream = stream
        atomic_json(self.directory / 'run_selection.json', {'retired': list(self.retired_scopes)})
        self.publish({**message, 'stream': self.active_stream})
        if self.active_stream and self.replay_pending_stream == self.active_stream:
            path = self.directory / (self.active_stream + '.events.jsonl')
            if path.exists():
                self.publish_records(self.active_stream, path, publish=False)
                self.replay_records(self.active_stream, path)
                self.replay_pending_stream = None
            else:
                self.replay_pending_stream = None
        return True

    def accept(self, message):
        key = message["stream"]
        if self.current_only and key != self.active_stream:
            return  # Old in-flight blocks must not repopulate the new run's log.
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("invalid CLI stream path")
        path = self.directory / (key + ".events.jsonl")
        data = message["data"]
        offset = message["offset"]
        size = path.stat().st_size if path.exists() else 0
        if offset == size:
            with path.open("ab") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            size += len(data)
        elif offset < size:
            with path.open("rb") as source:
                source.seek(offset)
                if (
                    source.read(min(len(data), size - offset))
                    != data[: min(len(data), size - offset)]
                ):
                    raise ValueError("conflicting CLI replay; journal was retained")
        # A duplicate following a failed fsync must be synced again before it is ACKed.
        if path.exists():
            with path.open("r+b") as durable:
                os.fsync(durable.fileno())
        # Gap: request the durable contiguous offset, without acknowledging missing bytes.
        self.acknowledge({"k": "cli_ack", "stream": key, "offset": size})
        if path.exists():
            self.publish_records(key, path)

    def publish_records(self, key, path, *, publish=True):
        checkpoint = self.directory / (key + ".display.json")
        state = (
            json.loads(checkpoint.read_text(encoding="utf-8"))
            if checkpoint.exists()
            else {"event": 0, "text": 0}
        )
        if not isinstance(state, dict):
            state = {"event": 0, "text": 0}
        text_path = self.directory / (key + ".manager.log")
        if not text_path.exists() or text_path.stat().st_size < state["text"]:
            state = {"event": 0, "text": 0}
        with (
            path.open("rb") as source,
            text_path.open("r+b" if text_path.exists() else "w+b") as text,
        ):
            # Repair only our generated projection after a crash before its checkpoint commit.
            text.truncate(state["text"])
            text.seek(state["text"])
            source.seek(state["event"])
            while True:
                start = source.tell()
                line = source.readline(self.max_record_bytes + 1)
                if not line or not line.endswith(b"\n"):
                    if len(line) > self.max_record_bytes:
                        self.error = "A CLI event exceeds 16 MiB; complete received bytes remain on disk"
                    return
                event = json.loads(line)
                stamp = int(event["source_stamp_ns"])
                if (
                    event.get("stream_id") != key
                    or stamp <= 0
                    or event.get("clock_domain") != "unix"
                    or not isinstance(event.get("text"), str)
                ):
                    raise ValueError(
                        "invalid source CLI event; received bytes remain on disk"
                    )
                text.write(event["text"].encode("utf-8"))
                text.flush()
                os.fsync(text.fileno())
                if publish:
                    self.publish(
                        {
                            "k": "cli_event",
                            "t": stamp,
                            "stream": key,
                            "offset": start,
                            "text": event["text"],
                            "level": event.get("level", "info"),
                        }
                    )
                atomic_json(checkpoint, {"event": source.tell(), "text": text.tell()})
                self.error = None

    def replay_records(self, key, path):
        records, size = deque(), 0
        with path.open('rb') as source:
            while True:
                offset = source.tell()
                line = source.readline(self.max_record_bytes + 1)
                if not line or not line.endswith(b'\n'):
                    break
                event = json.loads(line)
                if event.get('stream_id') != key or event.get('clock_domain') != 'unix':
                    raise ValueError('invalid selected CLI journal')
                text = event['text']
                stamp = int(event['source_stamp_ns'])
                if not isinstance(text, str) or stamp <= 0:
                    raise ValueError('invalid selected CLI event')
                count = len(text.encode('utf-8'))
                records.append(({'k': 'cli_event', 't': stamp, 'stream': key, 'offset': offset,
                                 'text': text, 'level': event.get('level', 'info')}, count))
                size += count
                while records and (len(records) > 2000 or size > 4 * 1024 * 1024):
                    size -= records.popleft()[1]
        for message, _ in records:
            self.publish(message)

    def status(self):
        return {"storage": str(self.directory), "error": self.error,
                "scope": self.active_scope, "stream": self.active_stream,
                "current_run_only": self.current_only}


def expand_status(message):
    if message.get("v", 1) != 1:
        raise ValueError("unsupported CLI status version")
    rows = []
    for index, received, age, hz, stamp, captured, detail in message["s"]:
        if not isinstance(index, int) or not 0 <= index < len(SENSORS):
            raise ValueError("invalid CLI sensor id")
        rows.append(
            {
                "name": SENSORS[index],
                "received": bool(received),
                "online_at_source": bool(received) and age <= 30,
                "age_at_snapshot_s": age / 10,
                "hz": hz / 10,
                "source_stamp_ns": str(stamp),
                "received_unix_ns": str(captured),
                "source_time_known": int(stamp) > 0,
                "detail": detail,
            }
        )
    return {
        "source_stamp_ns": str(message["t"]),
        "timestamp": {"sec": message["t"] // 10**9, "nsec": message["t"] % 10**9},
        "sensors": rows,
        "vehicle": message.get("vehicle", []),
        "log_backlog_bytes": message.get("backlog", 0),
        "collector_error": message.get("collector_error", ""),
        "sender_error": message.get("sender_error"),
        "source_warning": message.get("source_warning"),
        "current_run": message.get("current_run"),
        "collector_stale": message.get("collector_stale", False),
        "source_age_at_forward_s": message.get("source_age_s", 0),
    }
