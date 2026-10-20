"""Priority isolation without serial devices or lifecycle operations."""
from copy import deepcopy

import pytest

from fsd_radio.link import Outbox
from fsd_radio.protocol import unpack_message
from fsd_radio.rpc import RpcPeer


def test_command_bypasses_full_data_queue_and_log_traffic(config):
    box = Outbox(config)
    for number in range(config['link']['extension_queue']):
        box.extension({'type': 'bulk', 'number': number})
    box.update_aux('cli_log', {'k': 'cli_log', 'stream': 'a' * 32,
                              'offset': 0, 't': 1, 'data': b'log backlog'})
    box.update_aux('cli_run', {'k': 'cli_run', 'scope': 'b' * 32, 't': 1})
    box.extension({'type': 'cli_rpc', 'id': 'c' * 32}, priority=True)
    job = box.next_piece(100)
    assert job.key == '_command'
    assert len(box.extensions) == config['link']['extension_queue']


def test_bulk_transfer_resumes_after_command(config):
    cfg = deepcopy(config)
    cfg['link']['fragment_bytes'] = 16
    box = Outbox(cfg)
    box.extension({'type': 'bulk', 'text': 'abcdefghijklmnopqrstuvwxyz0123456789'})
    bulk = box.next_piece(100)
    assert len(bulk.pieces) > 1
    box.finish_piece(bulk, True)
    saved_index = bulk.index
    box.extension({'type': 'stop'}, priority=True)
    while box.commands:
        command = box.next_piece(100)
        assert command.key == '_command'
        box.finish_piece(command, True)
    resumed = box.next_piece(100)
    assert resumed is bulk and resumed.index == saved_index


def test_rpc_reserves_command_capacity_and_prioritizes_ack(config):
    tx, rx = Outbox(config), Outbox(config)
    delivered = []
    sender = RpcPeer(tx, 'request', lambda *_: None)
    receiver = RpcPeer(rx, 'reply', lambda ident, value: delivered.append(value))
    for number in range(6):
        ident = f'{number:032x}'
        sender.send(ident, {'id': ident, 'op': 'config.read', 'module': 'controller'})
    with pytest.raises(ValueError):
        sender.send('e' * 32, {'id': 'e' * 32, 'op': 'config.list'})
    ident = 'f' * 32
    sender.send(ident, {'id': ident, 'op': 'cli.stop'}, priority=True)
    sender.tick()
    message = unpack_message(tx.commands[0])
    assert message['body']['id'] == ident
    receiver.accept(message['body'])
    receiver.tick()
    assert delivered[0]['op'] == 'cli.stop'
    acknowledgement = unpack_message(rx.commands[0])['body']
    assert acknowledgement['type'] == 'cli_rpc_ack' and acknowledgement['p'] == 1
    sender.accept(acknowledgement)
    assert ident not in sender.outgoing
