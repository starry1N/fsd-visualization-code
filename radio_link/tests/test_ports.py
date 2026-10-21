from types import SimpleNamespace

import pytest

from fsd_radio.ports import resolve_pc_port


def port(device, vid=0x1A86, location='1-4'):
    return SimpleNamespace(device=device, vid=vid, pid=0x7523,
                           serial_number=None, location=location)


def test_usb_match_follows_com_number_and_excludes_other_devices():
    settings = {'pc_usb': {'vid': 0x1A86, 'pid': 0x7523}}
    assert resolve_pc_port(settings, [port('COM9', vid=0x067B), port('COM7')]) == 'COM7'


def test_ambiguous_and_missing_usb_adapter_are_not_guessed():
    with pytest.raises(OSError, match='Multiple'):
        resolve_pc_port({}, [port('COM3'), port('COM7', location='1-5')])
    with pytest.raises(OSError, match='not found'):
        resolve_pc_port({}, [])


def test_optional_location_selects_one_of_identical_adapters():
    settings = {'pc_usb': {'vid': 0x1A86, 'pid': 0x7523, 'location': '1-5'}}
    assert resolve_pc_port(settings, [port('COM3'), port('COM7', location='1-5')]) == 'COM7'
