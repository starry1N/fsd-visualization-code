"""Bounded, acknowledged JSON transfers over the existing small extension channel."""
import base64
from collections import OrderedDict
import json
import time
import zlib

MAX_JSON = 262144
CHUNK = 144
CONTROL_OPERATIONS = frozenset(('cli.check', 'cli.start', 'cli.stop', 'cli.status',
                                'cli.result', 'config.set'))


def valid_id(value):
    return isinstance(value, str) and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


class RpcPeer:
    def __init__(self, outbox, direction, receive):
        self.outbox, self.direction, self.receive = outbox, direction, receive
        self.outgoing = OrderedDict()
        self.incoming = OrderedDict()
        self.completed = OrderedDict()
        self.acks = {}
        self.error = None

    def send(self, ident, value, *, priority=False):
        if not valid_id(ident):
            raise ValueError('Invalid RPC identity')
        if ident in self.outgoing:
            return
        if len(self.outgoing) >= 8 or (not priority and sum(
                not item['priority'] for item in self.outgoing.values()) >= 6):
            raise ValueError('CLI transfer queue is full')
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_JSON:
            raise ValueError('CLI request/result exceeds 256 KiB')
        raw = zlib.compress(raw)
        self.outgoing[ident] = {'chunks': [base64.b64encode(raw[i:i + CHUNK]).decode('ascii')
                                         for i in range(0, len(raw), CHUNK)],
                                'index': 0, 'sent': -1e9, 'deadline': time.monotonic() + 180,
                                'priority': bool(priority)}

    def accept(self, body):
        kind = body.get('type')
        if kind not in ('cli_rpc', 'cli_rpc_ack'):
            return False
        ident, direction = body.get('id'), body.get('dir')
        if not valid_id(ident) or direction not in ('request', 'reply'):
            raise ValueError('Invalid CLI transfer header')
        index = body.get('i')
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError('Invalid CLI transfer index')
        if kind == 'cli_rpc_ack':
            current = self.outgoing.get(ident)
            if direction != self.direction or current is None:
                return True
            if index <= len(current['chunks']):
                if index == len(current['chunks']):
                    self.outgoing.pop(ident)
                elif index != current['index']:
                    current['index'], current['sent'] = index, -1e9
            return True
        if direction == self.direction:
            raise ValueError('CLI transfer direction mismatch')
        priority = body.get('p', 0)
        if type(priority) is not int or priority not in (0, 1):
            raise ValueError('Invalid CLI transfer priority')
        count = body.get('n')
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 2048 or index >= count:
            raise ValueError('Invalid CLI transfer length')
        if ident in self.completed:
            done_count, done_priority = self.completed[ident]
            self.acks[ident] = (direction, done_count, done_priority)
            return True
        try:
            chunk = base64.b64decode(body['data'], validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid CLI chunk encoding') from exc
        if not 1 <= len(chunk) <= CHUNK:
            raise ValueError('CLI chunk size exceeded')
        if ident not in self.incoming:
            if len(self.incoming) >= 8 or (not priority and sum(
                    not item['priority'] for item in self.incoming.values()) >= 6):
                raise ValueError('Too many incomplete CLI transfers')
            self.incoming[ident] = {'chunks': [], 'count': count, 'updated': time.monotonic(),
                                    'priority': priority}
        current = self.incoming[ident]
        if current['count'] != count or current['priority'] != priority:
            raise ValueError('Conflicting CLI transfer size')
        current['updated'] = time.monotonic()
        offset = len(current['chunks'])
        if index == offset:
            current['chunks'].append(chunk)
        elif index < offset and current['chunks'][index] != chunk:
            raise ValueError('Conflicting CLI retry')
        offset = len(current['chunks'])
        if offset == count:
            try:
                decoder = zlib.decompressobj()
                raw = decoder.decompress(b''.join(current['chunks']), MAX_JSON + 1)
                if len(raw) > MAX_JSON or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise ValueError('CLI result exceeds size limit or is incomplete')
                try:
                    value = json.loads(raw, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
                except (RecursionError, UnicodeError) as exc:
                    raise ValueError('Invalid CLI JSON nesting or encoding') from exc
                if not isinstance(value, dict) or value.get('id') != ident:
                    raise ValueError('CLI result identity mismatch')
                self.receive(ident, value)
            except Exception:
                self.incoming.pop(ident, None)
                self.acks[ident] = (direction, 0, priority)
                raise
            self.incoming.pop(ident)
            self.completed[ident] = (count, priority)
            while len(self.completed) > 128:
                self.completed.popitem(last=False)
        self.acks[ident] = (direction, offset, priority)
        return True

    def tick(self):
        now = time.monotonic()
        for ident, current in list(self.incoming.items()):
            if now - current['updated'] > 180:
                self.incoming.pop(ident)
        for ident, (direction, offset, priority) in sorted(list(self.acks.items()), key=lambda row: -row[1][2]):
            try:
                self.outbox.extension({'type': 'cli_rpc_ack', 'id': ident, 'dir': direction,
                                       'i': offset, 'p': int(priority)}, priority=bool(priority))
            except ValueError:
                continue
            self.acks.pop(ident)
        for ident, current in sorted(list(self.outgoing.items()), key=lambda row: not row[1]['priority']):
            if now > current['deadline']:
                self.outgoing.pop(ident)
                self.error = 'CLI transfer timeout; result unknown, request=' + ident
                continue
            if now - current['sent'] < 2:
                continue
            index = current['index']
            try:
                self.outbox.extension({'type': 'cli_rpc', 'id': ident, 'dir': self.direction,
                                       'i': index, 'n': len(current['chunks']),
                                       'p': int(current['priority']),
                                       'data': current['chunks'][index]}, priority=current['priority'])
            except ValueError:
                continue
            current['sent'] = now
