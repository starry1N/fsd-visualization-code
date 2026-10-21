"""Reconnect only the selected radio, with bounded queues and visible failures."""

import logging
import threading
from .link import Endpoint, Outbox
from .transport import SerialTransport
from .protocol import SLAVE
from .ports import resolve_pc_port

LOG = logging.getLogger(__name__)


class RadioWorker:
    def __init__(self, role, port, config, on_message, on_peer=None):
        self.role, self.port, self.config = role, port, config
        self.auto_port = role == SLAVE and str(port).lower() == 'auto'
        self.selected_port = None if self.auto_port else port
        self.on_message, self.on_peer = on_message, on_peer
        self.outbox = Outbox(config)
        self.append_rssi = config['serial'].get(
            'append_rssi_pc' if role == SLAVE else 'append_rssi_linux', False)
        self.endpoint = None
        self.error = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="e22-radio", daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            transport = None
            try:
                self.selected_port = None if self.auto_port else self.port
                if self.auto_port:
                    self.selected_port = resolve_pc_port(self.config['serial'])
                transport = SerialTransport(self.selected_port, self.config["serial"]["baud"],
                                            append_rssi=self.append_rssi)
                endpoint = Endpoint(
                    self.role, transport, self.config, self.on_message, self.on_peer
                )
                endpoint.outbox = self.outbox
                self.endpoint, self.error = endpoint, None
                LOG.info("radio connected: %s%s", self.selected_port,
                         ' (USB auto)' if self.auto_port else '')
                endpoint.run(self.stop_event)
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                LOG.warning("radio disconnected: %s", exc)
                # Old fragments cannot be completed in a new session. Extensions may
                # have reached the peer: never silently replay them after reconnect.
                with self.outbox.lock:
                    if self.outbox.extensions or self.outbox.commands:
                        LOG.warning(
                            "commands/extensions discarded after disconnect; delivery is unknown"
                        )
                    self.outbox.extensions.clear()
                    self.outbox.commands.clear()
                    self.outbox.active.clear()
                    self.outbox.last.clear()
                self.endpoint = None
            finally:
                if transport:
                    transport.close()
            self.stop_event.wait(1)

    def status(self):
        endpoint = self.endpoint
        rssi = (endpoint.transport.rssi_status(self.config['foxglove']['stale_seconds'])
                if endpoint else {'enabled': self.append_rssi, 'valid': False, 'raw': None,
                                  'display_raw': -4, 'captured_at_unix_ns': None,
                                  'age_s': None, 'samples': 0,
                                  'packet_session': None, 'packet_seq': None,
                                  'clock_domain': 'unix', 'time_origin': 'local_uart_capture'})
        return {
            "connected": endpoint is not None,
            "port": self.selected_port,
            'port_selection': 'auto' if self.auto_port else 'explicit',
            "error": self.error,
            **(endpoint.status() if endpoint else {}),
            'rssi': rssi,
        }

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=6)
