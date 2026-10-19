"""ROS-independent bounded marker state, transforms and display quantization."""

from collections import OrderedDict
import math
from .protocol import pack_message
from .essentials import (
    CONES,
    STRIP,
    SEGMENTS,
    POINTS,
    BOX,
    ELLIPSOID,
    CYLINDER,
    ARROW,
    FIXED_COLOR_LAYERS,
    profile_tag,
    rgb_code,
)

IDENTITY = (0.0, 0.0, 0.0, 1.0)


def quaternion(q):
    norm = math.sqrt(sum(v * v for v in q))
    if not math.isfinite(norm) or norm < 1e-9:
        raise ValueError("invalid quaternion")
    return tuple(v / norm for v in q)


def multiply(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (
        w * X + x * W + y * Z - z * Y,
        w * Y - x * Z + y * W + z * X,
        w * Z + x * Y - y * X + z * W,
        w * W - x * X - y * Y - z * Z,
    )


def rotate(q, p):
    x, y, z, w = q
    a = multiply(multiply(q, (*p, 0.0)), (-x, -y, -z, w))
    return a[:3]


def transform_point(transform, point):
    t, q = transform
    return tuple(a + b for a, b in zip(t, rotate(q, point)))


def sample_indices(size, limit, pairs=False):
    if pairs:
        return [
            j
            for i in sample_indices(size // 2, max(1, limit // 2))
            for j in (2 * i, 2 * i + 1)
        ]
    if size <= limit:
        return list(range(size))
    return (
        [round(i * (size - 1) / (limit - 1)) for i in range(limit)]
        if limit > 1
        else [0]
    )


class MarkerCache:
    def __init__(self, capacity=256):
        self.capacity = capacity
        self.items = OrderedDict()
        self.source_stamp = 0
        self.frame_changes = 0
        self.evicted = 0

    def apply(self, markers, ros_now_ns):
        # Compare complete batches: MarkerArray objects may have different source times.
        batch_stamp = max((m["stamp"] for m in markers), default=0)
        if batch_stamp and self.source_stamp and batch_stamp < self.source_stamp:
            self.items.clear()
        if batch_stamp:
            self.source_stamp = batch_stamp
        for marker in markers:
            key = (marker["ns"], marker["id"])
            if marker["action"] == 3:
                self.items.clear()
            elif marker["action"] == 2:
                self.items.pop(key, None)
            elif marker["action"] == 0:
                lifetime = marker["lifetime_ns"]
                expires = ros_now_ns + lifetime if lifetime > 0 else None
                self.items.pop(key, None)
                self.items[key] = (marker, expires)
                if len(self.items) > self.capacity:
                    self.items.popitem(last=False)
                    self.evicted += 1

    def live(self, ros_now_ns):
        for key, (_, expires) in list(self.items.items()):
            if expires is not None and ros_now_ns >= expires:
                del self.items[key]
        return [entry[0] for entry in self.items.values()]


def compact_marker(marker, lookup, step, max_points, text_chars):
    if marker["type"] not in range(10):
        raise ValueError(
            "mesh/triangle markers are not supported by the bounded profile"
        )
    position = marker["position"]
    rotation = quaternion(marker["orientation"])
    if not marker["frame"] or not all(
        math.isfinite(v) for v in (*position, *marker["scale"])
    ):
        raise ValueError("empty frame or non-finite marker")
    # Wireless snapshots freeze geometry at its source time, including frame_locked markers.
    transform = lookup(marker["frame"], marker["stamp"])
    pos = transform_point(transform, position)
    ori = multiply(transform[1], rotation)
    points = marker["points"]
    indices = sample_indices(len(points), max_points, marker["type"] == 5)
    selected = []
    selected_colors = []
    for index in indices:
        point = points[index]
        if not all(math.isfinite(v) for v in point):
            if marker["type"] in (4, 5):
                raise ValueError("invalid line point; refusing to bridge a gap")
            continue
        selected.append([round(v / step) for v in point])
        if marker["colors"] and index < len(marker["colors"]):
            selected_colors.append(marker["colors"][index])
    # Point coordinates remain local to transformed pose: no invented sensor TF.
    return [
        f"{marker['ns']}:{marker['id']}",
        marker["type"],
        [round(v / step) for v in pos],
        [round(v, 4) for v in ori],
        [round(max(0.0, v), 3) for v in marker["scale"]],
        marker["color"],
        selected,
        selected_colors,
        marker["text"][:text_chars],
    ]


def essential_marker(name, marker, lookup, step, max_points):
    if marker["type"] == 9:
        return None  # Labels and speed text are generated locally.
    if marker["color"][3] == 0 and not any(c[3] for c in marker["colors"]):
        return None
    old = compact_marker(marker, lookup, step, max_points, 0)
    _, typ, position, rotation, scale, rgba, local_points, colors, _ = old

    def world(point):
        rotated = rotate(rotation, [v * step for v in point])
        return [round((p * step + v) / step) for p, v in zip(position, rotated)]

    points = [world(p) for p in local_points]
    kind = None
    dimensions = False
    if typ in (4, 5):
        if len(points) < 2:
            return None
        kind = STRIP if typ == 4 else SEGMENTS
    elif typ == 0:
        kind = ARROW
        if len(points) < 2:
            points = [position, world([scale[0] / step, 0, 0])]
        else:
            points = points[:2]
            colors = colors[:2]
    elif typ == 8 or typ == 7 and name not in ("map", "fused"):
        kind = POINTS
    elif name in ("map", "fused") and typ in (1, 2, 3, 6, 7):
        kind = CONES
        if typ not in (6, 7):
            points = [position]
    else:
        kind = {1: BOX, 2: ELLIPSOID, 3: CYLINDER, 6: BOX}[typ]
        dimensions = True
        if typ != 6:
            points = [position]
    if not points:
        return None
    record = {"h": kind, "p": points, "t": marker["stamp"]}
    if name not in FIXED_COLOR_LAYERS:
        codes = [rgb_code(c) for c in colors] if colors else [rgb_code(rgba)]
        record["c"] = [codes[0]] if all(c == codes[0] for c in codes) else codes
    if dimensions:
        record["d"] = [max(0, round(v / step)) for v in scale]
        if any(abs(a - b) > 1e-4 for a, b in zip(rotation, IDENTITY)):
            record["r"] = [round(v * 32767) for v in rotation]
    return record


def scene_snapshot(
    name, cache, lookup, config, ros_now_ns, source_age=0.0, diagnostics=None, epoch=0
):
    options = config["layers"][name]
    step = config["geometry"]["quantization_m"]
    live = cache.live(ros_now_ns)
    records = []
    stats = {
        "source_markers": len(live),
        "selected_markers": 0,
        "omitted_markers": 0,
        "source_points": sum(m.get("source_points", len(m["points"])) for m in live),
        "sent_points": 0,
        "tf_or_geometry_drops": 0,
        "cache_evictions": cache.evicted,
    }
    visible = [marker for marker in live if marker["type"] != 9]
    for index in sample_indices(len(visible), options["markers"]):
        try:
            record = essential_marker(
                name, visible[index], lookup, step, options["points"]
            )
            if record is not None:
                records.append(record)
        except (ValueError, LookupError):
            stats["tf_or_geometry_drops"] += 1
    message = {
        "e": 3,
        "ep": epoch,
        "k": "scene",
        "l": name,
        "g": profile_tag(config),
        "t": cache.source_stamp,
        "o": records,
        "a": round(source_age, 1),
    }
    while True:
        stats["selected_markers"] = len(records)
        stats["omitted_markers"] = max(0, len(live) - len(records))
        stats["sent_points"] = sum(len(record["p"]) for record in records)
        try:
            if (
                len(pack_message(message, config["link"]["max_message_bytes"]))
                <= config["geometry"]["snapshot_bytes"]
            ):
                if diagnostics is not None:
                    diagnostics.clear()
                    diagnostics.update(stats)
                return message
        except ValueError:
            pass
        # Thin all lines/point sets before discarding whole objects.
        changed = False
        for record in records:
            if len(record["p"]) > 4:
                indices = sample_indices(
                    len(record["p"]),
                    max(2, len(record["p"]) // 2),
                    record["h"] == SEGMENTS,
                )
                record["p"] = [record["p"][i] for i in indices]
                if len(record.get("c", [])) > 1:
                    record["c"] = [record["c"][i] for i in indices]
                changed = True
        if not changed:
            if not records:
                raise ValueError("snapshot budget cannot hold metadata")
            records.pop()
