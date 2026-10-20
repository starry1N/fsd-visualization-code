"""Appended RSSI parsing and freshness without opening any serial port."""
from copy import deepcopy

import pytest

from fsd_radio.configuration import validate
from fsd_radio.protocol import Framer, MASTER, Packet
from fsd_radio.transport import SerialTransport


def test_rssi_all_byte_values_in_coalesced_frames():
    packet = Packet(MASTER, 123, 7)
    wire = packet.encode()
    framer = Framer(append_rssi=True)
    stream = b''.join(wire + bytes([value]) for value in range(256))
    assert framer.feed(stream) == [packet] * 256
    assert framer.last_rssi['raw'] == 255
    assert framer.last_rssi['samples'] == 256
    assert framer.errors == 0


@pytest.mark.parametrize('raw', [0, 10, 126, 255])
def test_rssi_byte_can_arrive_in_a_separate_read(raw):
    packet = Packet(MASTER, 123, 9)
    framer = Framer(append_rssi=True)
    result = []
    for value in packet.encode():
        result.extend(framer.feed(bytes([value])))
    assert result == [packet]  # Do not delay protocol ACK until the trailer is read.
    assert framer.last_rssi is None
    assert framer.feed(bytes([raw])) == []
    assert framer.last_rssi['raw'] == raw
    assert framer.last_rssi['packet_seq'] == 9


def test_disabled_rssi_parser_keeps_plain_packet_behavior():
    packet = Packet(MASTER, 123, 8)
    framer = Framer()
    assert framer.feed(packet.encode() * 2) == [packet, packet]
    assert framer.last_rssi is None


def test_rssi_stale_does_not_display_previous_strength(monkeypatch):
    import fsd_radio.transport as transport
    monkeypatch.setattr(transport.time, 'monotonic', lambda: 100)
    instance = SerialTransport.__new__(SerialTransport)
    instance.framer = Framer(append_rssi=True)
    assert instance.rssi_status()['display_raw'] == -1
    instance.framer.feed(Packet(MASTER, 123, 9).encode() + b'\xa0')
    sample = instance.rssi_status()
    assert sample['valid'] and sample['display_raw'] == 160
    stamp = sample['captured_at_unix_ns']
    monkeypatch.setattr(transport.time, 'monotonic', lambda: 111)
    expired = instance.rssi_status()
    assert not expired['valid'] and expired['display_raw'] == -3
    assert expired['raw'] == 160 and expired['captured_at_unix_ns'] == stamp


def test_rssi_rejects_module_packet_smaller_than_application_frame(config):
    cfg = deepcopy(config)
    cfg['serial']['append_rssi_pc'] = True
    cfg['serial']['module_packet_bytes'] = 128
    with pytest.raises(ValueError, match='240'):
        validate(cfg)
