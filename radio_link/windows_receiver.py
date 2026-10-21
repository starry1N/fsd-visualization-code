"""Frozen Windows entry; delegates all reception/control to the existing PC code."""
import argparse
import logging
from pathlib import Path
import shutil
import sys

from fsd_radio.configuration import load_config
from fsd_radio.pc_receiver import run_pc


def resource_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def main():
    root = resource_root()
    home = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else root
    external = home / 'config/radio_link/radio.yaml'
    default_config = external if external.is_file() else root / 'radio_link/radio.yaml'
    parser = argparse.ArgumentParser(description='E22 Windows receiver / Foxglove server')
    parser.add_argument('--config', type=Path, default=default_config)
    parser.add_argument('--port', help='Override auto USB selection, e.g. COM3')
    parser.add_argument('--mode', choices=('native', 'bridge'))
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--export-config', type=Path, metavar='DIRECTORY',
                        help='Export editable defaults and Foxglove layout, then exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    try:
        if args.export_config:
            files = ('radio_link/radio.yaml', 'radio_link/foxglove_layout.json',
                     'src/fsd_visualization/config/topics.yaml',
                     'src/fsd_visualization/config/fsd.rviz')
            destination = args.export_config.resolve()
            if any((destination / name).exists() for name in files):
                raise ValueError('Export would overwrite existing configuration; choose an empty directory')
            for name in files:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with (root / name).open('rb') as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
            print(f'Configuration exported to {destination}')
            return 0
        print(f'Configuration: {args.config.resolve()}', flush=True)
        print('Connect Foxglove to ws://127.0.0.1:8765 (default). Ctrl+C to stop.', flush=True)
        config = load_config(args.config, args.mode)
        run_pc(config, args.port or config['serial']['pc_port'])
        return 0
    except Exception:
        logging.exception('Receiver failed')
        # Keep double-click failures readable; explicit CLI invocations never pause.
        if len(sys.argv) == 1 and sys.stdin is not None and sys.stdin.isatty():
            try:
                input('Press Enter to close...')
            except (EOFError, KeyboardInterrupt):
                pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
