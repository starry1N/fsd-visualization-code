"""Display summaries of authoritative messages; no driving or mission decisions."""
import math

COLORS = {0: '红', 1: '黄', 2: '蓝', 3: '橙', 4: '未知'}
STATES = ('IDLE', 'READY', 'INSPECTION', 'EXPLORE', 'MAPPING_DONE', 'RACE', 'FINISH', 'EMERGENCY')
MODES = ('Trackdrive', 'Skidpad', 'Acceleration', 'Inspection', 'EBS')
MAP_FIELDS = ('red_cones', 'yellow_cones', 'blue_cones', 'orange_cones', 'unknown_cones')


def number(value):
    value = float(value)
    return value if math.isfinite(value) else None


def json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def stamp_ns(message):
    header = getattr(message, 'header', None)
    return header.stamp.sec * 10**9 + header.stamp.nanosec if header else None


def map_cones(message):
    return [cone for field in MAP_FIELDS for cone in getattr(message, field)]


def summary(name, message):
    if name == 'mission':
        fields = dict(token.split('=', 1) for token in message.description.split() if '=' in token)
        return {'阶段': STATES[message.state] if message.state < len(STATES) else str(message.state),
                '赛项字段': MODES[message.mission_mode] if message.mission_mode < len(MODES) else str(message.mission_mode),
                '定位模式': 'EKF通道' if message.localization_mode == 0 else 'NDT',
                **{key: fields.get(key, '未知') for key in
                   ('mode_selected', 'go_pending', 'go_count', 'lap', 'gnss_status', 'gnss_ready',
                    'lidar_ready', 'localization_ready', 'map_closed', 'map_quality', 'centerline_ready',
                    'pose_fresh', 'confidence_ok', 'ndt_required', 'ndt_ready', 'wait')},
                '权威诊断': message.description}
    if name == 'fused':
        invalid = sum(not all(math.isfinite(v) for v in (c.position.x, c.position.y, c.position.z))
                      for c in message.cones)
        return {'锥桶数': len(message.cones), '无效坐标数': invalid}
    if name == 'map':
        return {**{COLORS[i]: len(getattr(message, field)) for i, field in enumerate(MAP_FIELDS)},
                '总数': len(map_cones(message)), '闭合': bool(message.is_closed)}
    if name in ('centerline', 'path'):
        speeds = [number(w.twist.twist.linear.x) for w in message.waypoints]
        speeds = [v for v in speeds if v is not None]
        return {'航点数': len(message.waypoints), '最小目标速度m/s': min(speeds) if speeds else None,
                '最大目标速度m/s': max(speeds) if speeds else None}
    if name == 'command':
        effort = number(message.throttle_brake)
        return {'目标速度m/s': number(message.speed), '转角deg': number(message.angle),
                '开度': effort, '指令语义': '无效值' if effort is None else
                '正驱动请求' if effort > 0 else '制动请求' if effort < 0 else '中性开度'}
    if name == 'pose':
        p, q = message.pose.position, message.pose.orientation
        norm2 = q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w
        yaw = math.degrees(math.atan2(2*(q.w*q.z + q.x*q.y), norm2 - 2*(q.y*q.y + q.z*q.z)))
        return {'X': number(p.x), 'Y': number(p.y), 'Z': number(p.z),
                '航向deg': number(yaw) if norm2 > 1e-12 else None}
    if name in ('localization_ready', 'global_ready', 'emergency'):
        return {'值': bool(message.data)}
    if name in ('localization_confidence', 'path_confidence'):
        return {'值': number(message.data)}
    if name == 'inspection':
        return {'ok': bool(message.ok), 'failures': list(message.failures)}
    return {}
