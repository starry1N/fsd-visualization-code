"""Reproducible same-scene comparison, including fragmentation and empty ACKs."""

import argparse
import json
import math
from pathlib import Path
from fsd_radio.configuration import load_config
from fsd_radio.essentials import profile_tag
from fsd_radio.geometry import MarkerCache, compact_marker, scene_snapshot
from fsd_radio.protocol import MASTER, SLAVE, Packet, pack_message


def marker(
    index, typ, position, points=(), color=(255, 255, 255, 255), scale=(0.25, 0.25, 0.4)
):
    return dict(
        ns="cone_map" if typ == 3 else "sampled_geometry",
        id=index,
        type=typ,
        action=0,
        stamp=1792248147046376568 + index * 1_000_000,
        frame="map",
        frame_locked=False,
        lifetime_ns=0,
        position=position,
        orientation=(0.0, 0.0, 0.0, 1.0),
        scale=scale,
        color=list(color),
        points=list(points),
        colors=[],
        text="",
    )


def cases(config):
    lookup = lambda *args: ((0, 0, 0), (0, 0, 0, 1))
    stamp = 1792248147046376568
    vehicle = dict(
        k="vehicle",
        f="map",
        t=stamp,
        q=0.05,
        p=[8, -96, -47],
        r=[0.0434, -0.051, 0.748, 0.6603],
        v=0.09,
        a=0.1,
    )
    essential = {k: v for k, v in vehicle.items() if k not in ("f", "q")}
    essential.update(e=3, ep=0x12345678, g=profile_tag(config), vt=stamp - 123456789)
    result = [("vehicle", vehicle, essential)]
    palette = [
        (255, 0, 0, 255),
        (0, 100, 255, 255),
        (255, 230, 0, 255),
        (255, 128, 0, 255),
    ]
    scenes = [
        (
            "map_24_cones",
            "map",
            [
                marker(i, 3, (i * 0.65, (-1) ** i * 2.5, 0.0), color=palette[i % 4])
                for i in range(24)
            ],
        ),
        (
            "path_48_points",
            "path",
            [
                marker(
                    0,
                    4,
                    (0, 0, 0),
                    [(i * 0.6, math.sin(i / 8) * 2, 0) for i in range(48)],
                    (0, 210, 255, 255),
                    (0.08, 0, 0),
                )
            ],
        ),
        (
            "guided_6_boxes",
            "guided",
            [
                marker(
                    i,
                    1,
                    (i * 1.25, 1.5, 0.3),
                    color=(0, 255, 0, 255),
                    scale=(0.5 + i * 0.05, 0.4, 0.7),
                )
                for i in range(6)
            ],
        ),
        ("empty_map", "map", []),
    ]
    for name, layer, markers in scenes:
        records = [
            compact_marker(m, lookup, 0.05, config["layers"][layer]["points"], 64)
            for m in markers
        ]
        count = sum(len(r[6]) for r in records)
        stats = dict(
            source_markers=len(records),
            selected_markers=len(records),
            omitted_markers=0,
            source_points=count,
            sent_points=count,
            tf_or_geometry_drops=0,
            cache_evictions=0,
        )
        legacy = dict(
            k="scene", l=layer, f="map", t=stamp, q=0.05, o=records, s=stats, a=0.1
        )
        cache = MarkerCache()
        cache.source_stamp = stamp
        cache.apply(markers, stamp)
        reduced = scene_snapshot(layer, cache, lookup, config, stamp, source_age=0.1)
        result.append((name, legacy, reduced))
    return result


def costs(message, size):
    raw = pack_message(message)
    pieces = [raw[i : i + size] for i in range(0, len(raw), size)]
    total = sum(
        len(
            Packet(
                MASTER, 1, i + 1, message_id=1, index=i, count=len(pieces), payload=p
            ).encode()
        )
        for i, p in enumerate(pieces)
    )
    total += len(pieces) * len(Packet(SLAVE, 2, 1, ack_session=1, ack_seq=1).encode())
    return dict(
        application_bytes=len(raw), fragments=len(pieces), two_way_uart_bytes=total
    )


def measure(config):
    report = []
    for name, old, new in cases(config):
        before, after = (
            costs(old, config["link"]["fragment_bytes"]),
            costs(new, config["link"]["fragment_bytes"]),
        )
        report.append(
            dict(
                case=name,
                before=before,
                schema2=costs(
                    {**new, "e": 2, "t": new["t"] // 1_000_000 * 1_000_000},
                    config["link"]["fragment_bytes"],
                ),
                after=after,
                uart_saving_percent=round(
                    100
                    * (1 - after["two_way_uart_bytes"] / before["two_way_uart_bytes"]),
                    1,
                ),
            )
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fragment-bytes", type=int, choices=range(16,145))
    args = parser.parse_args()
    config = load_config(Path(__file__).resolve().parents[1] / "radio.yaml")
    if args.fragment_bytes is not None:
        config["link"]["fragment_bytes"] = args.fragment_bytes
    print(json.dumps(measure(config), indent=2))
