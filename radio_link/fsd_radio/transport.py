"""One owner for each serial port; partial writes and disconnects are explicit."""

import time
import serial
from .protocol import Framer


class SerialTransport:
    def __init__(self, port, baud=115200, *, append_rssi=False):
        if not port:
            raise ValueError(
                "Linux radio port is required; use --port /dev/serial/by-id/..."
            )
        self.serial = serial.serial_for_url(
            port,
            baudrate=baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.05,
            write_timeout=2,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.framer = Framer(append_rssi=append_rssi)
        self.received = []
        self.tx_bytes = self.rx_bytes = 0

    def write(self, data):
        offset, deadline = 0, time.monotonic() + 2
        while offset < len(data):
            count = self.serial.write(data[offset:])
            if not count or time.monotonic() > deadline:
                raise OSError("serial write did not complete")
            offset += count
            self.tx_bytes += count
        self.serial.flush()

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.received:
                return self.received.pop(0)
            data = self.serial.read(min(max(self.serial.in_waiting, 1), 1024))
            self.rx_bytes += len(data)
            self.received.extend(self.framer.feed(data))
        return self.received.pop(0) if self.received else None

    def close(self):
        self.serial.close()

    def rssi_status(self, stale_seconds=10):
        sample = self.framer.last_rssi
        enabled = self.framer.append_rssi
        age = None if sample is None else max(0, time.monotonic() - sample['received_monotonic'])
        valid = bool(enabled and age is not None and age <= stale_seconds)
        return {
            'enabled': enabled, 'valid': valid, 'raw': sample['raw'] if sample else None,
            'display_raw': (-2 if not enabled else -1 if sample is None else
                            -3 if not valid else sample['raw']),
            'age_s': round(age, 2) if age is not None else None,
            'captured_at_unix_ns': sample['captured_at_unix_ns'] if sample else None,
            'clock_domain': 'unix', 'time_origin': 'local_uart_capture',
            'samples': sample['samples'] if sample else 0,
            'packet_session': sample['packet_session'] if sample else None,
            'packet_seq': sample['packet_seq'] if sample else None,
        }
