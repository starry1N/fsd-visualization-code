"""Single-file Windows build. No application/serial entry is executed here."""
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata

radio = Path(SPECPATH)
viewer = radio.parent
data, binaries, hidden = collect_all(
    'foxglove', filter_submodules=lambda name: not any(
        part in name.split('.') for part in ('tests', 'benchmarks', 'notebook', 'datasets')
    )
)
data += copy_metadata('foxglove-sdk')
data += copy_metadata('pyserial') + copy_metadata('PyYAML')
for license_name in ('LICENSE.txt', 'LICENSE_PYTHON.txt'):
    license_file = Path(sys.base_prefix) / license_name
    if license_file.is_file():
        data.append((str(license_file), 'licenses/python'))
data += [
    (str(radio / 'radio.yaml'), 'radio_link'),
    (str(radio / 'foxglove_layout.json'), 'radio_link'),
    (str(viewer / 'src/fsd_visualization/config/topics.yaml'), 'src/fsd_visualization/config'),
    (str(viewer / 'src/fsd_visualization/config/fsd.rviz'), 'src/fsd_visualization/config'),
    (str(viewer / 'src/fsd_visualization/fsd_visualization/configuration.py'),
     'src/fsd_visualization/fsd_visualization'),
]
analysis = Analysis(
    [str(radio / 'windows_receiver.py')], pathex=[str(radio)],
    binaries=binaries, datas=data,
    hiddenimports=hidden + ['serial.tools.list_ports_windows'],
    excludes=['rclpy', 'pytest', 'tkinter'],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
          name='FSD_E22_Receiver', console=True, debug=False,
          strip=False, upx=False)
