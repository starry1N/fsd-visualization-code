"""Bounded ASCII/base64 frames, CRC, fragmentation, reassembly and replay guard."""

from collections import OrderedDict
from dataclasses import dataclass
import base64
import binascii
import json
import struct
import time
import zlib

HEADER = struct.Struct(">BBIHIHHBB")
VERSION, MASTER, SLAVE = 1, 1, 2
MAX_PAYLOAD, MAX_FRAGMENTS, MAX_WIRE = 144, 64, 226


@dataclass(frozen=True)
class Packet:
    role: int
    session: int
    seq: int
    ack_session: int = 0
    ack_seq: int = 0
    message_id: int = 0
    index: int = 0
    count: int = 0
    payload: bytes = b""

    def encode(self):
        if not 0 < self.session <= 0xFFFFFFFF or self.role not in (MASTER, SLAVE):
            raise ValueError("invalid session or role")
        if len(self.payload) > MAX_PAYLOAD or not 0 <= self.count <= MAX_FRAGMENTS:
            raise ValueError("packet too large")
        if (self.count == 0 and (self.payload or self.index)) or (
            self.count and self.index >= self.count
        ):
            raise ValueError("invalid fragment")
        raw = (
            HEADER.pack(
                VERSION,
                self.role,
                self.session,
                self.seq,
                self.ack_session,
                self.ack_seq,
                self.message_id,
                self.index,
                self.count,
            )
            + self.payload
        )
        wire = b"~" + base64.b64encode(raw + struct.pack(">I", zlib.crc32(raw))) + b"\n"
        if len(wire) > MAX_WIRE:
            raise ValueError("wire packet too large")
        return wire

    @classmethod
    def decode(cls, wire):
        if (
            not wire.startswith(b"~")
            or not wire.endswith(b"\n")
            or len(wire) > MAX_WIRE
        ):
            raise ValueError("framing")
        try:
            raw = base64.b64decode(wire[1:-1], validate=True)
        except binascii.Error as exc:
            raise ValueError("base64") from exc
        if (
            len(raw) < HEADER.size + 4
            or zlib.crc32(raw[:-4]) != struct.unpack(">I", raw[-4:])[0]
        ):
            raise ValueError("CRC or length")
        version, *fields = HEADER.unpack(raw[: HEADER.size])
        if version != VERSION:
            raise ValueError("version")
        packet = cls(*fields, payload=raw[HEADER.size : -4])
        packet.encode()  # Same limits for incoming and outgoing packets.
        return packet


class Framer:
    def __init__(self, *, append_rssi=False):
        self.buffer = bytearray()
        self.errors = 0
        self.append_rssi = append_rssi
        self.rssi_packet = None
        self.last_rssi = None

    def feed(self, data):
        result = []
        for byte in data:
            if self.rssi_packet is not None:
                # Consume before interpreting '~' or newline: both are legal RSSI bytes.
                # Requires one application frame per E22 packet (240-byte module setting).
                packet = self.rssi_packet
                self.last_rssi = {
                    'raw': byte, 'captured_at_unix_ns': str(time.time_ns()),
                    'received_monotonic': time.monotonic(),
                    'packet_session': packet.session, 'packet_seq': packet.seq,
                    'samples': (self.last_rssi or {}).get('samples', 0) + 1,
                }
                self.rssi_packet = None
                continue
            if byte == ord("~"):
                self.buffer[:] = b"~"
            elif self.buffer:
                self.buffer.append(byte)
                if len(self.buffer) > MAX_WIRE:
                    self.buffer.clear()
                    self.errors += 1
                elif byte == 10:
                    try:
                        packet = Packet.decode(bytes(self.buffer))
                        result.append(packet)
                        if self.append_rssi:
                            self.rssi_packet = packet
                    except ValueError:
                        self.errors += 1
                    self.buffer.clear()
        return result


def pack_message(message, limit=4096):
    if message.get("k") in ("cli_log", "cli_ack"):
        from .cli_telemetry import encode_payload

        return encode_payload(message, limit)
    if message.get("e") in (2, 3):
        from .essentials import encode

        return encode(message, limit)
    raw = json.dumps(
        message, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    if len(raw) > limit:
        raise ValueError(f"message JSON exceeds {limit} bytes")
    compressed = zlib.compress(raw, 6)
    return b"Z" + compressed if len(compressed) < len(raw) else b"J" + raw


def unpack_message(data, limit=4096):
    if not data or len(data) > limit + 1:
        raise ValueError("message length")
    if data[:1] in (b"L", b"A", b"b"):
        from .cli_telemetry import decode_payload

        return decode_payload(data, limit)
    if data[:1] in (b"S", b"V", b"z"):
        from .essentials import decode

        return decode(data, limit)
    if data[:1] == b"Z":
        decoder = zlib.decompressobj()
        try:
            raw = decoder.decompress(data[1:], limit + 1)
        except zlib.error as exc:
            raise ValueError("invalid compressed message") from exc
        if (
            len(raw) > limit
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise ValueError("compressed message exceeds limit or is truncated")
    elif data[:1] == b"J":
        raw = data[1:]
    else:
        raise ValueError("message encoding")
    try:
        message = json.loads(
            raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
        )
    except (RecursionError, zlib.error) as exc:
        raise ValueError("invalid message nesting or compression") from exc
    if not isinstance(message, dict) or message.get("k") not in (
        "scene",
        "vehicle",
        "extension",
        "cli_status",
        "cli_run",
        "cli_control",
    ):
        raise ValueError("message kind")
    if message["k"] == "extension" and not isinstance(message.get("body"), dict):
        raise ValueError("extension body must be an object")
    return message


class Reassembler:
    def __init__(self, timeout=120.0, limit=4096):
        self.timeout, self.limit = timeout, limit
        self.active = OrderedDict()
        self.completed = OrderedDict()

    def accept(self, packet, now=None):
        now = time.monotonic() if now is None else now
        for key, entry in list(self.active.items()):
            if now - entry[0] > self.timeout:
                del self.active[key]
        if not packet.count:
            return None
        key = packet.session, packet.message_id
        if key in self.completed:
            return None
        if key not in self.active:
            if len(self.active) >= 12:
                self.active.popitem(last=False)
            self.active[key] = (now, packet.count, {})
        _, count, parts = self.active[key]
        if count != packet.count:
            del self.active[key]
            raise ValueError("fragment count changed")
        if packet.index in parts and parts[packet.index] != packet.payload:
            raise ValueError("fragment changed")
        parts[packet.index] = packet.payload
        if sum(map(len, parts.values())) > self.limit + 1:
            del self.active[key]
            raise ValueError("assembly too large")
        if len(parts) != count:
            return None
        del self.active[key]
        message = unpack_message(
            b"".join(parts[index] for index in range(count)), self.limit
        )
        self.completed[key] = now
        while len(self.completed) > 1024:
            self.completed.popitem(last=False)
        return message
