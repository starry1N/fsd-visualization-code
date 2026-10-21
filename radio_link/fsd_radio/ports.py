"""Select the configured USB adapter without opening or probing serial devices."""
from serial.tools import list_ports


def resolve_pc_port(serial_config, ports=None):
    selector = serial_config.get('pc_usb', {'vid': 0x1A86, 'pid': 0x7523})
    candidates = []
    for port in list_ports.comports() if ports is None else ports:
        if port.vid != selector['vid'] or port.pid != selector['pid']:
            continue
        if selector.get('serial_number') and port.serial_number != selector['serial_number']:
            continue
        if selector.get('location') and port.location != selector['location']:
            continue
        candidates.append(port.device)
    if not candidates:
        raise OSError('E22 USB adapter not found; connect the configured CH340 adapter')
    if len(candidates) != 1:
        raise OSError('Multiple matching E22 USB adapters: ' + ', '.join(sorted(candidates))
                      + '; select --port or serial.pc_usb.location/serial_number')
    return candidates[0]
