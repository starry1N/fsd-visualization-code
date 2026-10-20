"""CLI-owned mailbox transport and asynchronous Foxglove parameter cache.

No configuration writes or process management are performed by the radio adapter.
"""
from collections import deque
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from .cli_telemetry import atomic_json
from .rpc import RpcPeer


def request_hash(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


class LinuxControl:
    def __init__(self, config, outbox):
        self.directory = Path(config['cli']['project_root']) / '.runtime/fsd_manager/control'
        self.outbox = outbox
        self.peer = RpcPeer(outbox, 'reply', self.receive)
        self.pending = {}
        self.last_heartbeat = -1e9
        self.state = {}

    def receive(self, ident, request):
        try:
            if len(self.pending) >= 8:
                raise ValueError('CLI request queue is full')
            if not self.state.get('online'):
                raise ValueError('CLI管理进程未在线，请启动CLI常驻菜单')
            if request.get('boot') != self.state.get('boot'):
                raise ValueError('CLI已重启，请等待新状态')
            if int(request['expires_ns']) < time.time_ns():
                raise ValueError('CLI请求已过期')
            if request.get('op') not in ('cli.status', 'cli.result', 'cli.check', 'cli.start', 'cli.stop',
                                         'config.list', 'config.read', 'config.set'):
                raise ValueError('Unknown CLI operation')
            digest = request_hash(request)
            result_path = self.directory / 'results' / (ident + '.json')
            inbox = self.directory / 'inbox' / (ident + '.json')
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding='utf-8'))
                if result.get('request_hash') != digest:
                    raise ValueError('Request ID already belongs to another payload')
            elif inbox.exists():
                if request_hash(json.loads(inbox.read_text(encoding='utf-8'))) != digest:
                    raise ValueError('Conflicting queued request identity')
            else:
                if len(list((self.directory / 'inbox').glob('*.json'))) >= 32:
                    raise ValueError('CLI mailbox is full')
                atomic_json(inbox, request)
            self.pending[ident] = {'deadline': time.monotonic() + 180, 'hash': digest}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.peer.send(ident, {'id': ident, 'ok': False, 'stage': 'complete',
                                  'error': str(exc), 'source_stamp_ns': str(time.time_ns())})

    def tick(self):
        now = time.monotonic()
        if now - self.last_heartbeat >= 1:
            try:
                state = json.loads((self.directory / 'heartbeat.json').read_text(encoding='utf-8'))
                age = time.time_ns() - int(state['updated_ns'])
                state['online'] = bool(state.get('online') and 0 <= age <= 5_000_000_000)
                self.state = state
            except (OSError, ValueError, KeyError, TypeError):
                self.state = {'online': False, 'updated_ns': str(time.time_ns())}
            # Compact independent heartbeat also exists while the FSD stack is stopped.
            self.outbox.update_aux('cli_control', {
                'k': 'cli_control', 't': self.state['updated_ns'], 'clock_domain': 'unix',
                **{key: self.state.get(key) for key in
                   ('boot', 'online', 'phase', 'running', 'run', 'active', 'last', 'config_error')}
            }, interval=2, weight=3)
            self.last_heartbeat = now
        for ident, pending in list(self.pending.items()):
            path = self.directory / 'results' / (ident + '.json')
            result = None
            try:
                if path.exists():
                    candidate = json.loads(path.read_text(encoding='utf-8'))
                    if candidate.get('request_hash') != pending['hash']:
                        raise ValueError('CLI result identity conflict')
                    if candidate.get('stage') == 'complete':
                        result = candidate
                    elif candidate.get('boot') != self.state.get('boot'):
                        raise ValueError('CLI执行期间重启，结果未知；未重复执行，请检查状态')
                if result is None and now > pending['deadline']:
                    raise ValueError('CLI操作超时，结果未知；请查询状态，勿重复启动')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result = {'id': ident, 'ok': False, 'stage': 'complete', 'error': str(exc),
                          'source_stamp_ns': str(time.time_ns())}
            if result is not None:
                try:
                    self.peer.send(ident, result)
                except ValueError:
                    continue
                self.pending.pop(ident)
        self.peer.tick()


class PcControl:
    """SDK callbacks only change this locked cache; main loop performs radio work."""
    def __init__(self, outbox):
        self.peer = RpcPeer(outbox, 'request', self.receive)
        self.lock = threading.RLock()
        self.view = None
        self.heartbeat = {}
        self.boot = None
        self.retired_boots = deque(maxlen=16)
        self.received = -1e9
        self.cache = {'selection/module': 'controller', 'meta/status': '等待Linux CLI',
                      'meta/dirty_count': 0, 'meta/online': False}
        self.modules, self.files, self.drafts = {}, {}, {}
        self.queue = deque()
        self.pending = {}
        self.changed = True
        self.last_result = None
        self.saving = False
        self.last_publish = -1e9
        self.failed_reads = set()

    def online(self):
        source_age = (time.time_ns() - int(self.heartbeat.get('t') or 0)) / 1e9
        return bool(self.heartbeat.get('online') and time.monotonic() - self.received < 8
                    and -5 <= source_age <= 10)

    def note(self, value):
        self.cache['meta/status'] = str(value)
        self.cache['meta/dirty_count'] = sum(len(v) for v in self.drafts.values())
        self.changed = True

    def enqueue(self, body):
        if not isinstance(body, dict) or body.get('type') != 'cli':
            return False
        with self.lock:
            action = body.get('action')
            if action not in ('check', 'start', 'stop', 'save', 'reload', 'status'):
                raise ValueError('Unknown CLI action')
            if len(self.queue) >= 8:
                raise ValueError('CLI command queue is full')
            self.queue.append(action)
            self.note('本机已排队，等待Linux执行：' + action)
        return True

    def parameters(self, names=()):
        import foxglove
        with self.lock:
            return [foxglove.Parameter(name=key, value=value)
                    for key, value in self.cache.items() if not names or key in names]

    def set_parameters(self, parameters):
        with self.lock:
            try:
                if self.saving:
                    raise ValueError('正在保存，请等待Linux结果后编辑')
                proposed = {parameter.name: parameter.get_value() for parameter in parameters}
                for name, value in proposed.items():
                    if name == 'selection/module':
                        if value not in self.modules or not self.modules[value]['available']:
                            raise ValueError('请选择meta/modules中的可用模块')
                    elif name.startswith('yaml/') and name in self.cache:
                        module, path = name[5:].split('/', 1)
                        if module not in self.files:
                            raise ValueError('模块尚未读取')
                        old = self.files[module]['values']['/' + path]
                        # Linux repeats authoritative type and cross-module validation.
                        if type(value) is not type(old) and not (
                                isinstance(old, float) and type(value) is int):
                            raise ValueError('参数类型不匹配：' + name)
                    else:
                        raise ValueError('参数只读或不存在：' + name)
                for name, value in proposed.items():
                    self.cache[name] = value
                    if name.startswith('yaml/'):
                        module, path = name[5:].split('/', 1)
                        edits = self.drafts.setdefault(module, {})
                        if value == self.files[module]['values']['/' + path]:
                            edits.pop('/' + path, None)
                        else:
                            edits['/' + path] = value
                self.note('草稿尚未保存；点击“保存配置”提交，Linux确认后才算成功')
            except (ValueError, TypeError, KeyError) as exc:
                self.note(exc)
            return self.parameters()

    def accept_heartbeat(self, message):
        with self.lock:
            boot = message.get('boot')
            if boot and boot in self.retired_boots:
                return
            if boot and self.boot != boot:
                if self.boot:
                    self.retired_boots.append(self.boot)
                self.boot = boot
                unknown = any(p['op'] in ('cli.start', 'cli.stop', 'config.set') for p in self.pending.values())
                self.modules.clear()
                self.files.clear()
                self.drafts.clear()
                self.failed_reads.clear()
                self.queue.clear()
                self.pending.clear()
                self.peer.outgoing.clear()
                self.last_result = None
                self.saving = False
                self.cache = {'selection/module': 'controller', 'meta/online': False}
                self.note('CLI已切换，旧操作结果未知，请检查状态' if unknown else '正在读取CLI配置目录')
            self.heartbeat = message
            self.received = time.monotonic()
            self.changed = True

    def request(self, operation, **arguments):
        if not self.online():
            raise ValueError('CLI离线或状态过期，未发送指令')
        if len(self.pending) >= 8:
            raise ValueError('CLI request queue is full')
        ident = uuid.uuid4().hex
        body = {'id': ident, 'op': operation, 'boot': self.heartbeat['boot'],
                'run': self.heartbeat.get('run'),
                'expires_ns': str(time.time_ns() + 180_000_000_000),
                'source_stamp_ns': str(time.time_ns()), **arguments}
        self.peer.send(ident, body)
        self.pending[ident] = {'op': operation, 'args': arguments, 'deadline': time.monotonic() + 185}
        return ident

    def receive(self, ident, response):
        with self.lock:
            pending = self.pending.pop(ident, None)
            if pending is None:
                return
            op, args = pending['op'], pending['args']
            if op not in ('config.list', 'config.read') or not response.get('ok'):
                self.last_result = response
                self.cache['meta/last_request_id'] = ident
                self.cache['meta/last_action_ok'] = bool(response.get('ok'))
            if op == 'config.set':
                self.saving = False
            if not response.get('ok'):
                if op in ('config.list', 'config.read'):
                    self.failed_reads.add(args.get('module', '__catalog__'))
                self.note('Linux返回失败：' + str(response.get('error')))
                return
            result = response.get('result', {})
            if op == 'config.list':
                self.modules = {row['module']: row for row in result['modules']}
                self.cache['meta/modules'] = ', '.join(k for k, v in self.modules.items() if v['available'])
            elif op == 'config.read':
                module = result['module']
                values = {row['path']: row['value'] for row in result['parameters']}
                self.files[module] = {**result, 'values': values}
                for path, value in values.items():
                    self.cache['yaml/' + module + path] = self.drafts.get(module, {}).get(path, value)
                for key in ('source', 'revision', 'restart_required'):
                    self.cache['meta/' + module + '/' + key] = result[key]
            elif op == 'config.set':
                for module in result['saved']:
                    self.drafts.pop(module, None)
                    self.files.pop(module, None)
                    # Clear cache pending fresh revisions; stale values cannot be saved again.
                    self.cache = {k: v for k, v in self.cache.items()
                                  if not k.startswith(('yaml/' + module + '/', 'meta/' + module + '/'))}
                self.note(result.get('message', 'Linux已保存，重启相关进程后生效'))
                return
            if op in ('config.list', 'config.read'):
                self.cache['meta/loaded_modules'] = ', '.join(self.files)
                self.changed = True
                if not self.last_result:
                    self.note('已读取Linux配置；修改后点击“保存配置”提交')
            else:
                self.note('Linux操作完成：' + op + ('（启动流程已提交，查看phase确认就绪）' if op == 'cli.start' else ''))

    def tick(self):
        with self.lock:
            online = self.online()
            if self.cache.get('meta/online') != online:
                self.cache['meta/online'] = online
                self.changed = True
            if self.heartbeat:
                self.cache.update({
                    'meta/boot': str(self.heartbeat.get('boot') or ''),
                    'meta/phase': str(self.heartbeat.get('phase') or ''),
                    'meta/running': bool(self.heartbeat.get('running')),
                    'meta/active': str((self.heartbeat.get('active') or {}).get('op') or ''),
                })
            for ident, pending in list(self.pending.items()):
                if time.monotonic() > pending['deadline']:
                    self.pending.pop(ident)
                    if pending['op'] == 'config.set':
                        self.saving = False
                    self.failed_reads.add(pending['args'].get('module', '__catalog__'))
                    self.note('请求超时，结果未知；请查询CLI状态，勿重复启动。ID=' + ident)
            if online:
                try:
                    if self.queue:
                        action = self.queue.popleft()
                        if action == 'save':
                            if self.saving:
                                raise ValueError('配置已在保存中')
                            updates = [{'module': module, 'revision': self.files[module]['revision'],
                                        'changes': values} for module, values in self.drafts.items() if values]
                            if not updates:
                                raise ValueError('没有待保存的草稿')
                            self.request('config.set', updates=updates)
                            self.saving = True
                        elif action == 'reload':
                            if self.saving or self.pending:
                                raise ValueError('请等待当前请求结束后重新读取')
                            self.drafts.clear()
                            self.files.clear()
                            self.modules.clear()
                            self.failed_reads.clear()
                            self.cache = {k: v for k, v in self.cache.items() if not k.startswith(('yaml/', 'meta/'))}
                            self.note('已丢弃草稿，重新读取Linux文件')
                        else:
                            if action in ('check', 'start', 'stop') and any(
                                    p['op'] in ('cli.check', 'cli.start', 'cli.stop') for p in self.pending.values()):
                                raise ValueError('已有CLI操作等待结果，请勿重复操作')
                            self.request('cli.' + action)
                    outstanding = {(p['op'], p['args'].get('module')) for p in self.pending.values()}
                    if not self.modules and '__catalog__' not in self.failed_reads and ('config.list', None) not in outstanding:
                        self.request('config.list')
                    selected = self.cache['selection/module']
                    if selected in self.modules and self.modules[selected]['available'] and selected not in self.files and selected not in self.failed_reads and ('config.read', selected) not in outstanding:
                        self.request('config.read', module=selected)
                except (ValueError, KeyError, TypeError) as exc:
                    self.note(exc)
            elif self.queue:
                self.queue.clear()
                self.note('CLI离线，未发送排队操作；恢复连接后请重新操作')
            self.peer.tick()
            if self.peer.error:
                self.note(self.peer.error)
                self.peer.error = None
            if self.view and self.changed and time.monotonic() - self.last_publish >= .5:
                parameters = self.parameters()
                stamp = int(self.heartbeat.get('t') or 0)
                status = {
                    **self.heartbeat, 'online': online, 'status': self.cache.get('meta/status'),
                    'dirty_count': self.cache.get('meta/dirty_count', 0),
                    'pending': [{'id': k, 'op': v['op']} for k, v in self.pending.items()],
                    'result': self.last_result,
                    'source_stamp_ns': str(stamp), 'clock_domain': 'unix',
                }
                self.changed = False
                self.last_publish = time.monotonic()
            else:
                parameters = None
        # Do not hold our callback lock while entering SDK publication code.
        if parameters is not None:
            self.view.server.publish_parameter_values(parameters)
            self.view.log('cli/control', status, log_time=stamp)
