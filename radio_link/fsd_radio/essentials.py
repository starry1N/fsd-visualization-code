"""Essential geometry schemas 2/3 (source nanoseconds in schema 3). No SDK/ROS dependency or display styles on air."""

import binascii
import math
import struct
import zlib

LAYERS = (
    "vehicle",
    "fused",
    "control",
    "guided",
    "map",
    "centerline",
    "path",
    "trajectory",
)
CONES, STRIP, SEGMENTS, POINTS, BOX, ELLIPSOID, CYLINDER, ARROW = range(8)
FIXED_COLOR_LAYERS = {"centerline", "path", "trajectory", "control"}
SCHEMA = 3
HEADER = struct.Struct(">BB6sHH")
TIMED_HEADER = struct.Struct(">BBQHHI")


def profile_tag(config):
    value = (
        f"2:{config['fixed_frame']}:{config['geometry']['quantization_m']:.9g}".encode()
    )
    return binascii.crc_hqx(value, 0)


def rgb_code(rgba):
    return (
        (round(rgba[0] * 7 / 255) << 5)
        | (round(rgba[1] * 7 / 255) << 2)
        | round(rgba[2] * 3 / 255)
    )


def rgb_color(code):
    return [
        round((code >> 5) * 255 / 7),
        round(((code >> 2) & 7) * 255 / 7),
        round((code & 3) * 255 / 3),
        255,
    ]


def _unsigned(value):
    if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("integer outside uint32")
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return result


def _signed(value):
    if not isinstance(value, int) or not -(2**31) <= value < 2**31:
        raise ValueError("coordinate outside int32")
    return _unsigned((value << 1) ^ (value >> 31))


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, count):
        if self.offset + count > len(self.data):
            raise ValueError("truncated essential message")
        result = self.data[self.offset : self.offset + count]
        self.offset += count
        return result

    def unsigned(self):
        result = 0
        for shift in range(0, 35, 7):
            byte = self.take(1)[0]
            if shift == 28 and byte > 15:
                raise ValueError("varint overflow")
            result |= (byte & 127) << shift
            if not byte & 128:
                return result
        raise ValueError("invalid varint")

    def signed(self):
        value = self.unsigned()
        return (value >> 1) ^ -(value & 1)


def _time_delta(value):
    if not isinstance(value, int) or not -(2**63) <= value < 2**63:
        raise ValueError("timestamp delta outside int64")
    # Preserve every nanosecond; millisecond-aligned deltas get a compact unit tag.
    milliseconds = value % 1_000_000 == 0
    ticks = value // 1_000_000 if milliseconds else value
    value = (((ticks << 1) ^ (ticks >> 63)) << 1) | (0 if milliseconds else 1)
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return result


def _read_time_delta(reader):
    value = 0
    for shift in range(0, 70, 7):
        byte = reader.take(1)[0]
        if shift == 63 and byte > 3:
            raise ValueError("timestamp delta overflow")
        value |= (byte & 127) << shift
        if not byte & 128:
            zigzag = value >> 1
            delta = (zigzag >> 1) ^ -(zigzag & 1)
            delta *= 1 if value & 1 else 1_000_000
            if not -(2**63) <= delta < 2**63:
                raise ValueError("timestamp delta outside int64")
            return delta
    raise ValueError("invalid timestamp delta")


def source_stamp(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 2**63:
        raise ValueError("source timestamp must be nonnegative int64 nanoseconds")
    return value


def _rotation(values):
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ValueError("invalid rotation")
    norm = math.sqrt(sum(v * v for v in values))
    if norm < 1e-9:
        raise ValueError("zero rotation")
    return [round(v / norm * 32767) for v in values]


def _check_record(record):
    kind = record["h"]
    points = record["p"]
    if (
        not isinstance(kind, int)
        or not 0 <= kind <= ARROW
        or not 1 <= len(points) <= 256
    ):
        raise ValueError("invalid essential geometry")
    if (
        kind == STRIP
        and len(points) < 2
        or kind == SEGMENTS
        and len(points) % 2
        or kind == ARROW
        and len(points) != 2
    ):
        raise ValueError("invalid line endpoints")
    if any(len(point) != 3 for point in points):
        raise ValueError("point must have three coordinates")
    colors = record.get("c", [])
    if colors and (
        len(colors) not in (1, len(points))
        or any(not isinstance(c, int) or not 0 <= c <= 255 for c in colors)
    ):
        raise ValueError("invalid RGB332 indices")
    dimensions = record.get("d")
    if kind in (BOX, ELLIPSOID, CYLINDER) and (
        dimensions is None or len(dimensions) != 3
    ):
        raise ValueError("physical dimensions required")
    if dimensions and (
        kind not in (BOX, ELLIPSOID, CYLINDER)
        or any(not isinstance(v, int) or not 0 <= v <= 0xFFFFFFFF for v in dimensions)
    ):
        raise ValueError("unexpected physical dimensions")
    rotation = record.get("r")
    if rotation is not None:
        if (
            kind not in (BOX, ELLIPSOID, CYLINDER)
            or len(rotation) != 4
            or any(not isinstance(v, int) or not -32767 <= v <= 32767 for v in rotation)
            or sum(v * v for v in rotation) < 1
        ):
            raise ValueError("invalid shape orientation")


def encode(message, limit=4096):
    vehicle = message["k"] == "vehicle"
    layer = 0 if vehicle else LAYERS.index(message["l"])
    if not vehicle and layer == 0:
        raise ValueError("vehicle is not a scene layer")
    version = message.get("e", SCHEMA)
    if version not in (2, 3):
        raise ValueError("unsupported essential schema")
    exact_stamp = source_stamp(message["t"])
    stamp = exact_stamp // 1_000_000
    source_age = message.get("a", 0)
    if (
        not isinstance(stamp, int)
        or not 0 <= stamp < 2**48
        or not math.isfinite(source_age)
    ):
        raise ValueError("timestamp or source age outside essential schema")
    if not isinstance(message["g"], int) or not 0 <= message["g"] <= 65535:
        raise ValueError("invalid profile tag")
    epoch = message.get("ep", 0)
    if not isinstance(epoch, int) or not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("invalid clock epoch")
    age = max(0, min(65535, round(source_age * 10)))
    result = bytearray(b"V" if vehicle else b"S")
    if version == 3:
        result += TIMED_HEADER.pack(
            version, layer, exact_stamp, age, message["g"], epoch
        )
    else:
        result += HEADER.pack(
            version, layer, stamp.to_bytes(6, "big"), age, message["g"]
        )
    if vehicle:
        for value in message["p"]:
            result += _signed(value)
        if len(message["p"]) != 3:
            raise ValueError("invalid vehicle position")
        result += struct.pack(">4h", *_rotation(message["r"]))
        speed = message.get("v")
        if speed is not None and (not math.isfinite(speed) or not 0 <= speed <= 655.34):
            raise ValueError("invalid speed")
        result += struct.pack(">H", 65535 if speed is None else round(speed * 100))
        if version == 3:
            result += _time_delta(source_stamp(message["vt"]) - exact_stamp)
    else:
        records = message["o"]
        if len(records) > 256:
            raise ValueError("too many objects")
        result += _unsigned(len(records))
        for record in records:
            _check_record(record)
            if version == 3:
                result += _time_delta(source_stamp(record["t"]) - exact_stamp)
            colors = record.get("c", [])
            flags = (
                (1 if "d" in record else 0)
                | (2 if "r" in record else 0)
                | (4 if len(colors) == 1 else 8 if colors else 0)
            )
            result += bytes((record["h"], flags)) + _unsigned(len(record["p"]))
            previous = [0, 0, 0]
            for point in record["p"]:
                for value, old in zip(point, previous):
                    result += _signed(value - old)
                previous = point
            result += bytes(colors)
            if flags & 1:
                for value in record["d"]:
                    result += _unsigned(value)
            if flags & 2:
                result += struct.pack(">4h", *record["r"])
    if len(result) > limit:
        raise ValueError("essential message exceeds size limit")
    compressed = b"z" + zlib.compress(result, 6)
    return compressed if len(compressed) < len(result) else bytes(result)


def decode(data, limit=4096):
    if data[:1] == b"z":
        decoder = zlib.decompressobj()
        try:
            data = decoder.decompress(data[1:], limit + 1)
        except zlib.error as exc:
            raise ValueError("invalid essential compression") from exc
        if (
            len(data) > limit
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise ValueError("essential decompression size or framing")
    if not data or data[:1] not in (b"S", b"V") or len(data) > limit:
        raise ValueError("essential encoding")
    reader = Reader(data)
    vehicle = reader.take(1) == b"V"
    if len(data) < 2:
        raise ValueError("truncated essential header")
    version = data[1]
    header = TIMED_HEADER if version == 3 else HEADER
    fields = header.unpack(reader.take(header.size))
    version, layer, stamp, age, tag = fields[:5]
    exact_stamp = (
        source_stamp(stamp)
        if version == 3
        else int.from_bytes(stamp, "big") * 1_000_000
    )
    if version not in (2, 3) or layer >= len(LAYERS) or (layer == 0) != vehicle:
        raise ValueError("essential schema or layer")
    message = {
        "e": version,
        "k": "vehicle" if vehicle else "scene",
        "t": exact_stamp,
        "a": age / 10,
        "g": tag,
    }
    if version == 3:
        message["ep"] = fields[5]
    if vehicle:
        message["p"] = [reader.signed() for _ in range(3)]
        rotation = struct.unpack(">4h", reader.take(8))
        norm = math.sqrt(sum(v * v for v in rotation))
        if norm < 1:
            raise ValueError("zero vehicle rotation")
        message["r"] = [v / norm for v in rotation]
        speed = struct.unpack(">H", reader.take(2))[0]
        message["v"] = None if speed == 65535 else speed / 100
        if version == 3:
            message["vt"] = source_stamp(exact_stamp + _read_time_delta(reader))
    else:
        message.update(l=LAYERS[layer], o=[])
        count = reader.unsigned()
        if count > 256:
            raise ValueError("too many objects")
        for _ in range(count):
            object_stamp = (
                source_stamp(exact_stamp + _read_time_delta(reader))
                if version == 3
                else None
            )
            kind, flags = reader.take(2)
            if flags & ~15 or flags & 12 == 12:
                raise ValueError("invalid geometry flags")
            size = reader.unsigned()
            if not 1 <= size <= 256:
                raise ValueError("invalid point count")
            points = []
            previous = [0, 0, 0]
            for _ in range(size):
                point = [old + reader.signed() for old in previous]
                if any(not -(2**31) <= v < 2**31 for v in point):
                    raise ValueError("coordinate overflow")
                points.append(point)
                previous = point
            record = {"h": kind, "p": points}
            if version == 3:
                record["t"] = object_stamp
            if flags & 12:
                record["c"] = list(reader.take(1 if flags & 4 else size))
            if flags & 1:
                record["d"] = [reader.unsigned() for _ in range(3)]
            if flags & 2:
                record["r"] = list(struct.unpack(">4h", reader.take(8)))
            _check_record(record)
            message["o"].append(record)
    if reader.offset != len(data):
        raise ValueError("trailing essential bytes")
    return message


def expanded_records(message, config):
    """Rebuild renderer records locally; IDs are snapshot-local, not sent on air."""
    if message["g"] != profile_tag(config):
        raise ValueError("fixed frame or quantization differs between endpoints")
    layer = message["l"]
    step = config["geometry"]["quantization_m"]
    styles = config["foxglove"]["styles"]
    default_color = styles["colors"].get(layer, styles["colors"]["default"])
    result = []
    stamps = []
    for index, record in enumerate(message["o"]):
        before = len(result)
        kind, points = record["h"], record["p"]
        codes = record.get("c", [])
        base = rgb_color(codes[0]) if codes else default_color
        colors = [rgb_color(c) for c in codes] if len(codes) > 1 else []
        rotation = record.get("r", [0, 0, 0, 32767])
        norm = math.sqrt(sum(v * v for v in rotation))
        rotation = [v / norm for v in rotation]
        size = [v * step for v in record.get("d", [])]
        ident = f"{layer}:{index}"
        if kind in (CONES, BOX, ELLIPSOID, CYLINDER):
            typ = {CONES: 3, BOX: 1, ELLIPSOID: 2, CYLINDER: 3}[kind]
            for j, point in enumerate(points):
                result.append(
                    [
                        f"{ident}:{j}",
                        typ,
                        point,
                        rotation,
                        styles["cone_size"] if kind == CONES else size,
                        colors[j] if colors else base,
                        [],
                        [],
                        "",
                    ]
                )
        else:
            typ = {STRIP: 4, SEGMENTS: 5, POINTS: 8, ARROW: 0}[kind]
            size = (
                styles["arrow_size"]
                if kind == ARROW
                else [styles["point_size"]] * 3
                if kind == POINTS
                else [styles["line_width"], 0, 0]
            )
            # Arrow two-point semantics interpret x/y as diameters, z as head length.
            if kind == ARROW:
                size = [styles["arrow_shaft"], styles["arrow_head"], 0]
            result.append(
                [ident, typ, [0, 0, 0], [0, 0, 0, 1], size, base, points, colors, ""]
            )
        stamps.extend([record.get("t", message["t"])] * (len(result) - before))
    message["object_stamps"] = stamps
    return result
