"""CLI-owned mailbox transport and asynchronous Foxglove parameter cache.

No configuration writes or process management are performed by the radio adapter.
"""
from collections import deque
import hashlib
import json
import re
from pathlib import Path
import threading
import time
import uuid

from .cli_telemetry import atomic_json
from .rpc import RpcPeer, CONTROL_OPERATIONS

EXIT_MEANINGS = {
    0: '正常结束', 1: '启动或运行失败，查看日志', 2: '参数或命令用法错误',
    126: '命令无法执行', 127: '命令不存在或子进程无法启动',
    130: 'SIGINT 中断（可能为主动停止）', 143: 'SIGTERM 请求终止',
    137: 'SIGKILL 强制终止（原因见日志）',
    -2: 'SIGINT 中断（可能为主动停止）', -15: 'SIGTERM 请求终止',
    -9: 'SIGKILL 强制终止（原因见日志）',
    134: 'SIGABRT 异常终止', -6: 'SIGABRT 异常终止',
    139: 'SIGSEGV 段错误', -11: 'SIGSEGV 段错误',
}

CLI_DISPLAY_PHASES = frozenset((
    '操作中', '等待操作', '已停止', '运行中', '并行检查中', '预检查通过',
    '启动中', '构建中', '预检查失败：可修复后重试', '启动失败：可修复后重试',
    '清理未完成；请 p 认证后重试 x / q', '其他退出码',
))


def runtime_exit(heartbeat):
    code = heartbeat.get('runtime_exit_code')
    if type(code) is not int:
        # Rolling upgrade: old CLI may only include the code in its phase text.
        match = re.search(r'启动任务结束，退出码\s*(-?\d+)', str(heartbeat.get('phase', '')))
        code = int(match[1]) if match else None
    if code is None:
        return None, None, None
    meaning = EXIT_MEANINGS.get(code, '其他子进程退出码，查看日志')
    return code, f'启动任务退出 {code}：{meaning}', ('退出码:' + str(code) if code in EXIT_MEANINGS else '其他退出码')


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
        priority = isinstance(request.get('op'), str) and request['op'] in CONTROL_OPERATIONS
        try:
            if len(self.pending) >= 8 or (not priority and sum(
                    not item['priority'] for item in self.pending.values()) >= 6):
                raise ValueError('CLI request queue is full')
            if not self.state.get('online'):
                raise ValueError('CLI管理进程未在线，请启动CLI常驻菜单')
            if request.get('boot') != self.state.get('boot'):
                raise ValueError('CLI已重启，请等待新状态')
            if int(request['expires_ns']) < time.time_ns():
                raise ValueError('CLI请求已过期')
            if request.get('op') not in ('cli.status', 'cli.result', 'cli.check', 'cli.start', 'cli.stop',
                                         'config.list', 'config.read', 'config.set', 'cli.cycle_mode'):
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
            self.pending[ident] = {'deadline': time.monotonic() + 180, 'hash': digest,
                                   'priority': priority}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.peer.send(ident, {'id': ident, 'ok': False, 'stage': 'complete',
                                  'error': str(exc), 'source_stamp_ns': str(time.time_ns())}, priority=priority)

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
                   ('boot', 'online', 'phase', 'running', 'run', 'active', 'last', 'config_error',
                    'runtime_exit_code', 'selected_mode', 'active_mode')}
            }, interval=2, weight=3)
            self.last_heartbeat = now
        for ident, pending in sorted(list(self.pending.items()), key=lambda row: not row[1]['priority']):
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
                    self.peer.send(ident, result, priority=pending['priority'])
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
        self.parameter_outcome = '等待'
        self.parameter_detail = '等待Linux参数源'

    def parameter_note(self, outcome, detail):
        self.parameter_outcome, self.parameter_detail = outcome, str(detail)
        self.changed = True

    def parameter_state(self, online):
        if not self.heartbeat:
            return '等待'
        if not online:
            return '离线'
        if self.saving or 'save' in self.queue:
            return '保存中'
        if self.parameter_outcome in ('失败', '结果未知'):
            return self.parameter_outcome
        if any(p['op'] in ('config.list', 'config.read') for p in self.pending.values()):
            return '读取中'
        if any(self.drafts.values()):
            return '未保存'
        return self.parameter_outcome

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
            if action not in ('check', 'start', 'stop', 'save', 'reload', 'status', 'cycle_mode'):
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
                self.parameter_note('就绪', '草稿已更新，修改后需点击保存配置')
            except (ValueError, TypeError, KeyError) as exc:
                self.note(exc)
                self.parameter_note('失败', exc)
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
                unknown = any(p['op'] in ('cli.start', 'cli.stop', 'config.set', 'cli.cycle_mode') for p in self.pending.values())
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
                self.parameter_note('等待', 'CLI已切换，正在重新读取参数')
            self.heartbeat = message
            self.received = time.monotonic()
            self.changed = True

    def request(self, operation, **arguments):
        if not self.online():
            raise ValueError('CLI离线或状态过期，未发送指令')
        priority = operation in CONTROL_OPERATIONS
        if len(self.pending) >= 8 or (not priority and sum(
                item['op'] not in CONTROL_OPERATIONS for item in self.pending.values()) >= 6):
            raise ValueError('CLI request queue is full')
        ident = uuid.uuid4().hex
        body = {'id': ident, 'op': operation, 'boot': self.heartbeat['boot'],
                'run': self.heartbeat.get('run'),
                'expires_ns': str(time.time_ns() + 180_000_000_000),
                'source_stamp_ns': str(time.time_ns()), **arguments}
        self.peer.send(ident, body, priority=priority)
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
                if op.startswith('config.'):
                    self.parameter_note('失败', response.get('error'))
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
                self.parameter_note('已保存', result.get('message', 'Linux已保存，需重启相关进程生效'))
                return
            if op in ('config.list', 'config.read'):
                if self.parameter_outcome != '已保存':
                    self.parameter_note('就绪', 'Linux参数已读取')
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
                    if pending['op'].startswith('config.'):
                        self.parameter_note('结果未知', '请求超时，请重新读取或查询原结果。ID=' + ident)
                    self.failed_reads.add(pending['args'].get('module', '__catalog__'))
                    self.note('请求超时，结果未知；请查询CLI状态，勿重复启动。ID=' + ident)
            if online:
                action = None
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
                            self.parameter_note('保存中', '等待Linux校验与保存结果')
                        elif action == 'reload':
                            if self.saving or self.pending:
                                raise ValueError('请等待当前请求结束后重新读取')
                            self.drafts.clear()
                            self.files.clear()
                            self.modules.clear()
                            self.failed_reads.clear()
                            self.cache = {k: v for k, v in self.cache.items() if not k.startswith(('yaml/', 'meta/'))}
                            self.note('已丢弃草稿，重新读取Linux文件')
                            self.parameter_note('等待', '已丢弃草稿，正在重新读取Linux文件')
                        else:
                            if action in ('check', 'start', 'stop', 'cycle_mode') and any(
                                    p['op'] in ('cli.check', 'cli.start', 'cli.stop', 'cli.cycle_mode') for p in self.pending.values()):
                                raise ValueError('已有CLI操作等待结果，请勿重复操作')
                            if action == 'cycle_mode':
                                mode = self.heartbeat.get('selected_mode')
                                if mode not in ('normal', 'brake_bench', 'manual_record'):
                                    raise ValueError('CLI模式未知或此会话不可切换，请更新CLI并等待状态')
                                self.request('cli.cycle_mode', expected_mode=mode)
                            else:
                                self.request('cli.' + action)
                    outstanding = {(p['op'], p['args'].get('module')) for p in self.pending.values()}
                    if not self.modules and '__catalog__' not in self.failed_reads and ('config.list', None) not in outstanding:
                        self.request('config.list')
                    selected = self.cache['selection/module']
                    if selected in self.modules and self.modules[selected]['available'] and selected not in self.files and selected not in self.failed_reads and ('config.read', selected) not in outstanding:
                        self.request('config.read', module=selected)
                except (ValueError, KeyError, TypeError) as exc:
                    self.note(exc)
                    if action in ('save', 'reload'):
                        self.parameter_note('失败', exc)
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
                # Automatic parameter reads do not make the CLI operation indicator busy.
                busy = bool(online and (
                    (self.heartbeat.get('active') or {}).get('op') in CONTROL_OPERATIONS
                    or any(item['op'] in CONTROL_OPERATIONS for item in self.pending.values())
                    or any(action in ('check', 'start', 'stop', 'status', 'save', 'cycle_mode') for action in self.queue)))
                exit_code, exit_detail, exit_state = runtime_exit(self.heartbeat)
                phase = str(self.heartbeat.get('phase') or '未知状态')
                show_exit = (not self.heartbeat.get('running') and exit_state and
                             (phase == '已停止' or phase.startswith('启动任务结束，退出码')))
                status = {
                    **self.heartbeat, 'online': online, 'status': self.cache.get('meta/status'),
                    'busy': busy,
                    'mode_display': ('等待CLI状态' if not self.heartbeat else '离线' if not online else
                                     {'normal': '正常', 'hardware': '正常', 'brake_bench': '制动台架',
                                      'manual_record': '有人跑动记录', 'sensor_only': '仅传感器'}.get(
                                         self.heartbeat.get('active_mode') if self.heartbeat.get('running')
                                         else self.heartbeat.get('selected_mode'), '未知模式')),
                    'parameter_state': self.parameter_state(online),
                    'parameter_detail': self.parameter_detail,
                    'runtime_exit_code': exit_code,
                    'exit_detail': exit_detail,
                    'exit_code_origin': 'start_full_fsd_child' if exit_code is not None else None,
                    'display_state': ('等待CLI状态' if not self.heartbeat else
                                      '离线' if not online else
                                      '操作中' if busy else
                                      exit_state if show_exit else phase),
                    'dirty_count': self.cache.get('meta/dirty_count', 0),
                    'pending': [{'id': k, 'op': v['op']} for k, v in self.pending.items()],
                    'result': self.last_result,
                    'source_stamp_ns': str(stamp), 'clock_domain': 'unix',
                }
                state_with_mode = status['display_state']
                if online:
                    if state_with_mode not in CLI_DISPLAY_PHASES and not state_with_mode.startswith('退出码:'):
                        state_with_mode = '其他CLI状态'
                    state_with_mode += ' · ' + status['mode_display']
                status['display_state_with_mode'] = state_with_mode
                self.changed = False
                self.last_publish = time.monotonic()
            else:
                parameters = None
        # Do not hold our callback lock while entering SDK publication code.
        if parameters is not None:
            self.view.server.publish_parameter_values(parameters)
            self.view.log('cli/control', status, log_time=stamp)
