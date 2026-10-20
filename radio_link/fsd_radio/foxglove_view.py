"""Official SDK typed SceneUpdate channels; the PC does not require ROS."""

from collections import deque
import json
import math
import queue
import threading
import time
import foxglove
from foxglove import messages as m
from foxglove.channels import SceneUpdateChannel, LogChannel
from foxglove.websocket import Capability, ServerListener
from .geometry import multiply, rotate
from .essentials import expanded_records, profile_tag, source_stamp
from .cli_telemetry import expand_status

TX_TOPIC = "/fsd_radio/extension/tx"


def timestamp(ns):
    return m.Timestamp(sec=ns // 10**9, nsec=ns % 10**9)


def vector(values):
    return m.Vector3(x=values[0], y=values[1], z=values[2])


def pose(position=(0, 0, 0), rotation=(0, 0, 0, 1)):
    return m.Pose(
        position=vector(position),
        orientation=m.Quaternion(
            x=rotation[0], y=rotation[1], z=rotation[2], w=rotation[3]
        ),
    )


def color(values):
    return m.Color(
        r=values[0] / 255, g=values[1] / 255, b=values[2] / 255, a=values[3] / 255
    )


def arrow_rotation(direction):
    length = math.sqrt(sum(v * v for v in direction))
    if length < 1e-8:
        return (0, 0, 0, 1), 0.0
    x, y, z = (v / length for v in direction)
    if x < -0.999999:
        return (0, 0, 1, 0), length
    norm = math.sqrt(2 * (1 + x))
    return (0, -z / norm, y / norm, norm / 2), length


def entity_from_record(record, frame, step, now_ns, lifetime, source_stamp):
    ident, typ, p, q, scale, rgba, points, point_colors, text = record
    position = [v * step for v in p]
    points = [[v * step for v in point] for point in points]
    origin = pose(position, q)
    c = color(rgba)
    fields = dict(
        timestamp=timestamp(source_stamp),
        frame_id=frame,
        id=ident,
        lifetime=m.Duration(sec=int(lifetime), nsec=int(lifetime % 1 * 1e9)),
        metadata=[
            m.KeyValuePair(key="source_ros_stamp_ns", value=str(source_stamp)),
            m.KeyValuePair(key="display", value="quantized low-bandwidth sample"),
        ],
    )
    if typ in (4, 5):
        fields["lines"] = [
            m.LinePrimitive(
                type=m.LinePrimitiveLineType.LineStrip
                if typ == 4
                else m.LinePrimitiveLineType.LineList,
                pose=origin,
                thickness=max(scale[0], 0.01),
                points=[m.Point3(x=p[0], y=p[1], z=p[2]) for p in points],
                color=c,
                colors=[color(v) for v in point_colors],
            )
        ]
    elif typ in (6, 7, 8):
        primitives = []
        for index, point in enumerate(points):
            world = [a + b for a, b in zip(position, rotate(q, point))]
            cls = m.CubePrimitive if typ == 6 else m.SpherePrimitive
            primitives.append(
                cls(
                    pose=pose(world, q),
                    size=vector([max(v, 0.03) for v in scale]),
                    color=color(point_colors[index]) if point_colors else c,
                )
            )
        if typ == 6:
            fields["cubes"] = primitives
        else:
            fields["spheres"] = primitives
    elif typ == 1:
        fields["cubes"] = [m.CubePrimitive(pose=origin, size=vector(scale), color=c)]
    elif typ == 2:
        fields["spheres"] = [
            m.SpherePrimitive(pose=origin, size=vector(scale), color=c)
        ]
    elif typ == 3:
        fields["cylinders"] = [
            m.CylinderPrimitive(
                pose=origin, size=vector(scale), bottom_scale=1, top_scale=1, color=c
            )
        ]
    elif typ == 9:
        fields["texts"] = [
            m.TextPrimitive(
                pose=origin,
                billboard=True,
                font_size=max(scale[2], 0.1),
                color=c,
                text=text,
            )
        ]
    elif typ == 0:
        if len(points) >= 2:
            rotation, length = arrow_rotation(
                [b - a for a, b in zip(points[0], points[1])]
            )
            start = [a + b for a, b in zip(position, rotate(q, points[0]))]
            origin = pose(start, multiply(q, rotation))
            head_length = min(scale[2] or length * 0.23, length)
            shaft_diameter, head_diameter = scale[:2]
        else:
            length, shaft_diameter, head_diameter = scale
            head_length = length * 0.23
        fields["arrows"] = [
            m.ArrowPrimitive(
                pose=origin,
                shaft_length=max(0, length - head_length),
                shaft_diameter=max(0.02, shaft_diameter),
                head_length=head_length,
                head_diameter=max(0.04, head_diameter),
                color=c,
            )
        ]
    return m.SceneEntity(**fields)


class PublishListener(ServerListener):
    def __init__(self, enqueue):
        self.enqueue = enqueue
        self.channels = {}
        self.errors = queue.Queue(maxsize=32)
        self.subscriptions = queue.Queue(maxsize=128)

    def on_subscribe(self, client, channel):
        try:
            self.subscriptions.put_nowait(channel.topic)
        except queue.Full:
            pass

    def on_client_advertise(self, client, channel):
        if channel.topic == TX_TOPIC and channel.encoding == "json":
            self.channels[(client.id, channel.id)] = True

    def on_client_unadvertise(self, client, client_channel_id):
        self.channels.pop((client.id, client_channel_id), None)

    def on_message_data(self, client, client_channel_id, data):
        try:
            if (client.id, client_channel_id) not in self.channels:
                raise ValueError(f"only {TX_TOPIC} with json encoding is accepted")
            if len(data) > 512:
                raise ValueError("extension exceeds 512 bytes")
            self.enqueue(json.loads(data))
        except (ValueError, TypeError) as exc:
            try:
                self.errors.put_nowait(str(exc))
            except queue.Full:
                pass


class FoxgloveView:
    def __init__(self, config, enqueue):
        self.config = config
        self.context = foxglove.Context()
        self.listener = PublishListener(enqueue)
        self._create_channels()
        self.cli_log_cache = deque()
        self.cli_log_cache_bytes = 0
        self.cli_status_received = None
        self.cli_collector_stale = True
        self.cli_scope = None
        self.cli_run_state = None
        self.snapshots = {}
        self.pending = []
        self.replay = {}
        self.clock_epoch = None
        self.retired_epochs = set()
        self.last_published = {}
        self.last_sample = {}
        self.latest_source_ns = 0
        self.timing = {
            "late_drops": 0,
            "unknown_source_times": 0,
            "buffer_overflows": 0,
        }
        self.buffer_seconds = config["foxglove"].get("alignment_buffer_seconds", 3.0)
        self.lock = threading.RLock()
        self.server = foxglove.start_server(
            name="FSD E22",
            host=config["foxglove"]["host"],
            port=config["foxglove"]["port"],
            context=self.context,
            capabilities=[Capability.ClientPublish],
            supported_encodings=["json"],
            server_listener=self.listener,
        )

    def _create_channels(self):
        self.channels = {
            name: SceneUpdateChannel("/radio/scene/" + name, context=self.context)
            for name in self.config["layers"]
        }
        self.json_channels = {
            name: foxglove.Channel("/radio/" + name, context=self.context)
            for name in (
                "state",
                "speed",
                "link",
                "layers",
                "extension/rx",
                "extension/status",
                "cli/sensors",
                "cli/vehicle",
                "cli/transfer",
                "cli/health",
            )
        }
        self.cli_log_channel = LogChannel("/radio/cli/log", context=self.context)

    def _reset_session(self, scope):
        # A new serverInfo session clears Foxglove's topic registry as well as history.
        # Existing SDK channels are not automatically advertised again by clear_session.
        self.server.clear_session('fsd-run-' + scope)
        for channel in (*self.channels.values(), *self.json_channels.values(), self.cli_log_channel):
            channel.close()
        self._create_channels()
        # Replay must reference the newly advertised channels, never closed handles.
        self.replay = {
            '/radio/' + name: (channel, self.replay['/radio/' + name][1],
                              self.replay['/radio/' + name][2])
            for name, channel in self.json_channels.items()
            if not name.startswith('cli/') and '/radio/' + name in self.replay
        }

    def log(self, name, value, log_time=None):
        stamp = self.latest_source_ns if log_time is None else log_time
        channel = self.json_channels[name]
        self.replay["/radio/" + name] = (channel, value, stamp)
        channel.log(value, log_time=stamp)

    def _queue_sample(self, key, message, stamp, received):
        source_stamp(stamp)
        # Zero means unavailable in this protocol; never relabel it as receipt time.
        if not stamp:
            self.timing["unknown_source_times"] += 1
            return
        signature = repr({k: v for k, v in message.items() if k != "a"})
        if self.last_sample.get(key) == signature:
            return
        self.last_sample[key] = signature
        if len(self.pending) >= 512:
            self.pending.pop(0)
            self.timing["buffer_overflows"] += 1
        self.pending.append((received, stamp, key, message))

    def flush(self, now=None):
        now = time.monotonic() if now is None else now
        ready = [
            event for event in self.pending if now - event[0] >= self.buffer_seconds
        ]
        if not ready:
            return
        cutoff = max(event[1] for event in ready)
        ready = [event for event in self.pending if event[1] <= cutoff]
        self.pending = [event for event in self.pending if event[1] > cutoff]
        for received, stamp, key, message in sorted(ready, key=lambda e: e[1]):
            if stamp < self.last_published.get(key, 0):
                self.timing["late_drops"] += 1
                continue
            self.last_published[key] = stamp
            if key != "extension/rx":
                self.latest_source_ns = max(self.latest_source_ns, stamp)
            if key == "speed":
                self.log("speed", message, stamp)
            elif key == "extension/rx":
                self.log(key, message, stamp)
            else:
                self.render(key, message, now - received)

    def clear(self):
        last_stamp = self.latest_source_ns
        with self.lock:
            self.snapshots.clear()
            self.pending.clear()
            self.replay.clear()
            self.last_published.clear()
            self.last_sample.clear()
            self.latest_source_ns = 0
        now = last_stamp
        for channel in self.channels.values():
            channel.log(
                m.SceneUpdate(
                    deletions=[
                        m.SceneEntityDeletion(
                            timestamp=timestamp(now), type=m.SceneEntityDeletionType.All
                        )
                    ]
                ),
                log_time=now,
            )

    def accept(self, message):
        received = time.monotonic()
        if message["k"] == "cli_run":
            if message == self.cli_run_state:
                return
            self.cli_run_state = dict(message)
            if message['scope'] != self.cli_scope:
                self.cli_scope = message['scope']
                self.cli_log_cache.clear()
                self.cli_log_cache_bytes = 0
                self.cli_status_received = None
                self.cli_collector_stale = True
                # Native Log panels otherwise retain the previous startup's rows.
                self._reset_session(self.cli_scope)
                for channel, value, stamp in list(self.replay.values()):
                    channel.log(value, log_time=stamp)
                for key, (snapshot, captured) in list(self.snapshots.items()):
                    self.render(key, snapshot, max(0, received - captured))
            self.log('cli/transfer', {'source_stamp_ns': str(message['t']),
                                     'current_run': message, 'scope': 'current_run_only'}, message['t'])
            return
        if message["k"] == "cli_event":
            stamp = source_stamp(message["t"])
            if stamp == 0:
                raise ValueError("CLI event has unknown source time")
            level = {
                "debug": m.LogLevel.Debug,
                "warn": m.LogLevel.Warning,
                "error": m.LogLevel.Error,
                "fatal": m.LogLevel.Fatal,
            }.get(message["level"], m.LogLevel.Info)
            value = m.Log(
                timestamp=timestamp(stamp),
                level=level,
                message=message["text"],
                name="CLI:" + message["stream"],
                file=str(message["offset"]),
            )
            # CLI event history must never be dropped by the geometry late-data policy.
            self.cli_log_channel.log(value, log_time=stamp)
            size = len(message["text"].encode("utf-8"))
            self.cli_log_cache.append((value, stamp, size))
            self.cli_log_cache_bytes += size
            while self.cli_log_cache and (
                len(self.cli_log_cache) > 2000
                or self.cli_log_cache_bytes > 4 * 1024 * 1024
            ):
                self.cli_log_cache_bytes -= self.cli_log_cache.popleft()[2]
            return
        if message["k"] == "cli_status":
            value = expand_status(message)
            stamp = source_stamp(message["t"])
            self.cli_status_received = received
            self.cli_collector_stale = value["collector_stale"]
            self.log(
                "cli/sensors", {k: v for k, v in value.items() if k != "vehicle"}, stamp
            )
            self.log(
                "cli/vehicle",
                {
                    "source_stamp_ns": str(stamp),
                    "clock_domain": "unix",
                    "rows": value["vehicle"],
                },
                stamp,
            )
            self.log(
                "cli/transfer",
                {
                    "source_stamp_ns": str(stamp),
                    "backlog_bytes": value["log_backlog_bytes"],
                    "collector_error": value["collector_error"],
                    "sender_error": value["sender_error"],
                    "source_warning": value["source_warning"],
                    "current_run": value["current_run"],
                    "scope": "current_run_only",
                },
                stamp,
            )
            return
        if message["k"] == "extension":
            payload = {
                **message["body"],
                "source_stamp_ns": str(message.get("t", 0)),
                "clock_domain": message.get("clock_domain", "unknown"),
                "time_origin": message.get("time_origin", "unknown"),
            }
            self._queue_sample("extension/rx", payload, message.get("t", 0), received)
            self.flush(received)
            return
        if self.clock_epoch is not None and message.get("e") != 3:
            self.timing["late_drops"] += 1
            return
        if message.get("e") == 3:
            epoch = message.get("ep", 0)
            if epoch in self.retired_epochs:
                self.timing["late_drops"] += 1
                return
            if self.clock_epoch is not None and epoch != self.clock_epoch:
                self.retired_epochs.add(self.clock_epoch)
                if len(self.retired_epochs) > 64:
                    self.retired_epochs.pop()
                self.clear()
            self.clock_epoch = epoch
        if message.get("e") in (2, 3):
            if message["g"] != profile_tag(self.config):
                raise ValueError(
                    "fixed frame or quantization differs between endpoints"
                )
            message = {
                **message,
                "f": self.config["fixed_frame"],
                "q": self.config["geometry"]["quantization_m"],
            }
            if message["k"] == "scene":
                message["o"] = expanded_records(message, self.config)
        key = message.get("l", "vehicle")
        if key not in self.channels:
            raise ValueError("unknown scene layer")
        with self.lock:
            previous = self.snapshots.get(key)
            if previous is None or message["t"] >= previous[0]["t"]:
                self.snapshots[key] = (message, received)
        self._queue_sample(key, message, message["t"], received)
        if message["k"] == "vehicle":
            speed_stamp = message.get("vt", 0)
            speed_value = {
                "speed_m_s": message.get("v"),
                "source_stamp_ns": str(speed_stamp),
                "timestamp": {"sec": speed_stamp // 10**9, "nsec": speed_stamp % 10**9},
            }
            self._queue_sample("speed", speed_value, speed_stamp, received)
        self.flush(received)

    def render(self, key, message, age):
        now = message["t"]
        lifetime = self.config["foxglove"]["stale_seconds"]
        frame = message["f"]
        source_age = age + message.get("a", 0)
        speed_stamp = message.get("vt", 0)
        speed_skew = abs(now - speed_stamp) / 1e9 if speed_stamp else None
        matched_speed = (
            message.get("v")
            if speed_skew is not None
            and speed_skew
            <= self.config["foxglove"].get("speed_alignment_tolerance_seconds", 0.2)
            else None
        )
        if message["k"] == "vehicle":
            if source_age > lifetime:
                self.log(
                    "state",
                    {
                        "stale": True,
                        "speed_m_s": None,
                        "receive_age_s": age,
                        "source_stamp_ns": str(now),
                        "timestamp": {"sec": now // 10**9, "nsec": now % 10**9},
                    },
                    log_time=now,
                )
                records = []
            else:
                self.log(
                    "state",
                    {
                        "stale": False,
                        "speed_m_s": matched_speed,
                        "speed_source_stamp_ns": str(speed_stamp),
                        "speed_pose_skew_s": speed_skew,
                        "source_ros_stamp_ns": message["t"],
                        "source_stamp_ns": str(message["t"]),
                        "timestamp": {"sec": now // 10**9, "nsec": now % 10**9},
                        "receive_age_s": round(age, 2),
                        "pose": message["p"],
                        "orientation": message["r"],
                        "frame_id": frame,
                    },
                    log_time=now,
                )
                records = [
                    [
                        "vehicle",
                        0,
                        message["p"],
                        message["r"],
                        self.config["foxglove"]["styles"]["arrow_size"],
                        self.config["foxglove"]["styles"]["colors"]["vehicle"],
                        [],
                        [],
                        "",
                    ]
                ]
        else:
            records = message["o"]
            # Live observations clear on stale; map/planned/trajectory remain, with age in /radio/layers.
            if key in ("guided", "fused", "control") and source_age > lifetime:
                records = []
        stamps = message.get("object_stamps", [message["t"]] * len(records))
        entities = [
            entity_from_record(
                r,
                frame,
                message["q"],
                now,
                lifetime if key in ("guided", "fused", "control", "vehicle") else 0,
                stamp,
            )
            for r, stamp in zip(records, stamps)
            if stamp > 0
        ]
        update = m.SceneUpdate(
            deletions=[
                m.SceneEntityDeletion(
                    timestamp=timestamp(max(0, now - 1)),
                    type=m.SceneEntityDeletionType.All,
                )
            ],
            entities=entities,
        )
        self.replay["/radio/scene/" + key] = (self.channels[key], update, now)
        self.channels[key].log(update, log_time=now)

    def refresh(self):
        # Emit each source sample once. Diagnostic refreshes never create source samples.
        now = time.monotonic()
        self.flush(now)
        while not self.listener.subscriptions.empty():
            topic = self.listener.subscriptions.get_nowait()
            if topic == "/radio/cli/log":
                for value, stamp, _ in self.cli_log_cache:
                    self.cli_log_channel.log(value, log_time=stamp)
                continue
            if topic.startswith('/radio/scene/'):
                key = topic.removeprefix('/radio/scene/')
                cached_scene = self.snapshots.get(key)
                if cached_scene:
                    snapshot, captured = cached_scene
                    self.render(key, snapshot, max(0, now - captured))
                continue
            cached = self.replay.get(topic)
            if cached:
                channel, value, stamp = cached
                channel.log(value, log_time=stamp)
        cli_age = (
            None if self.cli_status_received is None else now - self.cli_status_received
        )
        self.log(
            "cli/health",
            {
                "received": cli_age is not None,
                "receive_age_s": cli_age,
                "stale": self.cli_collector_stale
                or cli_age is None
                or cli_age
                > max(20, self.config.get("cli", {}).get("status_interval", 10) * 2),
            },
        )
        ages = {key: {"received": False, "stale": True} for key in self.channels}
        with self.lock:
            snapshots = list(self.snapshots.items())
        for key, (message, received) in snapshots:
            age = now - received
            ages[key] = {
                "received": True,
                "receive_age_s": round(age, 2),
                "source_age_lower_bound_s": round(age + message.get("a", 0), 2),
                "stale": age + message.get("a", 0)
                > self.config["foxglove"]["stale_seconds"],
                "source_ros_stamp_ns": message["t"],
                "source_stamp_ns": str(message["t"]),
                "source_time_known": message["t"] > 0,
                "sampling": message.get("s", {}),
                "schema": message.get("e", 1),
                "display_objects": len(message.get("o", [])),
            }
        self.log(
            "layers",
            {
                **ages,
                "timing": {
                    **self.timing,
                    "pending": len(self.pending),
                    "buffer_seconds": self.buffer_seconds,
                },
            },
        )
        if "vehicle" not in self.snapshots:
            self.log("state", {"stale": True, "speed_m_s": None, "receive_age_s": None})
        while not self.listener.errors.empty():
            self.log(
                "extension/status",
                {"accepted": False, "error": self.listener.errors.get_nowait()},
            )

    def close(self):
        self.server.stop()
