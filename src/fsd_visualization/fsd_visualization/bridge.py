"""Consume FSD outputs and publish display messages only."""
from collections import deque
from copy import deepcopy
import math
from pathlib import Path
import time
import signal
import json
import yaml
import rclpy
from rclpy.node import Node
from rclpy.clock import Clock, ClockType
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from rclpy.time import Time
from geometry_msgs.msg import TwistStamped, PoseStamped, PointStamped
from nav_msgs.msg import Odometry
from visualization_msgs.msg import Marker, MarkerArray
from std_msgs.msg import String, Bool, Float32
from tf2_ros import Buffer, TransformListener, TransformException
from tf2_geometry_msgs import do_transform_point
from .configuration import validate
from .diagnostics import summary, stamp_ns, map_cones, COLORS, json_safe


def finite_point(point):
    return all(math.isfinite(value) for value in (point.x, point.y, point.z))


def main():
    rclpy.init()
    node = None
    try:
        node = DisplayBridge()
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        # Terminal SIGINT and launch forwarding can arrive twice during teardown.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()


class DisplayBridge(Node):
    def __init__(self):
        super().__init__('fsd_display_bridge')
        self.declare_parameter('topics_config', '')
        self.declare_parameter('bridge_mode', False)
        self.declare_parameter('velocity_type', 'twist')
        self.settings = yaml.safe_load(Path(self.get_parameter('topics_config').value).read_text())
        validate(self.settings)
        self.topics = self.settings['topics']
        self.qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.subscriptions_owned = []
        self.last_speed = None
        self.last_received = None
        self.received = {}
        self.diagnostic_inputs = {}
        self.latest_fused = None
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.frame = self.settings['fixed_frame']
        self.points = deque(maxlen=int(self.settings['trajectory_max_points']))
        self.pose_frame = None
        self.pose_stamp = None
        self.last_pose_received = None
        self.last_ros_time = self.get_clock().now().nanoseconds
        self.speed_pub = self.create_publisher(Marker, '/fsd_visualization/speed', self.qos)
        self.status_pub = self.create_publisher(String, '/fsd_visualization/status', self.qos)
        velocity_type = self.get_parameter('velocity_type').value
        if velocity_type not in ('twist', 'odometry'):
            raise ValueError('velocity_type must be twist or odometry')
        self.velocity_topic = self.topics['velocity' if velocity_type == 'twist' else 'odometry']
        if self.velocity_topic:
            self.subscribe(TwistStamped if velocity_type == 'twist' else Odometry,
                           self.velocity_topic, self.speed)
        self.bridge_mode = self.get_parameter('bridge_mode').value
        from wuta_msgs.msg import ConeArray, ConeMap, MissionState, DevicesInspection
        from autoware_msgs.msg import Lane, Command
        for name, key, typ, label, output, color in [
                ('fused', 'fused_cones', ConeArray, '融合锥桶', 'fused_cones', None),
                ('map', 'cone_map', ConeMap, '锥桶地图', 'cone_map', None),
                ('centerline', 'centerline', Lane, '中心线', 'centerline', (0., 1., 0.)),
                ('path', 'final_waypoints', Lane, '最终路径', 'planned_path', (0., .8, 1.))]:
            publisher = self.create_publisher(MarkerArray, f'/fsd_visualization/{output}', self.qos) if self.bridge_mode else None
            def geometry(message, p=publisher, n=output, c=color):
                if p is not None:
                    self.cones(message, p, n) if c is None else self.lane(message, p, n, c)
            input_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE) if name == 'fused' else None
            self.subscribe_diagnostic(name, key, typ, label, geometry, qos=input_qos)
        if self.bridge_mode:
            self.trajectory_pub = self.create_publisher(MarkerArray, '/fsd_visualization/trajectory', self.qos)
        self.subscribe_diagnostic('pose', 'pose', PoseStamped, '定位位姿', self.pose if self.bridge_mode else None)
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        for name, typ, label in [
                ('mission', MissionState, '任务状态'), ('command', Command, '控制指令'),
                ('fusion_status', String, '融合诊断'), ('inspection', DevicesInspection, '设备自检'),
                ('localization_ready', Bool, '定位ready'),
                ('localization_confidence', Float32, '定位置信度'),
                ('global_ready', Bool, '全局中心线ready'),
                ('path_confidence', Float32, '路径置信度'), ('emergency', Bool, '急停事件')]:
            self.subscribe_diagnostic(name, name, typ, label,
                                      qos=latched if name in ('global_ready', 'path_confidence', 'emergency') else None)
        self.timer = self.create_timer(.1, self.render_speed,
                                      clock=Clock(clock_type=ClockType.STEADY_TIME))

    def subscribe(self, message_type, topic, callback, qos=None):
        self.received[topic] = None
        def receive(message):
            self.received[topic] = time.monotonic()
            callback(message)
        self.subscriptions_owned.append(self.create_subscription(message_type, topic, receive, qos or self.qos))

    def subscribe_diagnostic(self, name, key, message_type, label, callback=None, qos=None):
        topic = self.topics.get(key, '')
        self.diagnostic_inputs[name] = {'label': label, 'topic': topic, 'stamp_ns': None,
                                        'frame': '', 'values': None, 'error': None}
        if not topic:
            return
        def receive(message):
            entry = self.diagnostic_inputs[name]
            entry['stamp_ns'] = stamp_ns(message)
            entry['frame'] = getattr(getattr(message, 'header', None), 'frame_id', '')
            entry['error'] = None
            if name == 'fusion_status':
                try:
                    values = json.loads(message.data, parse_constant=lambda value: None)
                    if not isinstance(values, dict):
                        raise ValueError('JSON object required')
                    entry['values'] = json_safe(values)
                    entry['stamp_ns'] = values.get('stamp_ns') if isinstance(values.get('stamp_ns'), int) else None
                except (ValueError, TypeError):
                    entry['values'] = None
                    entry['error'] = '融合诊断JSON无效'
            else:
                entry['values'] = summary(name, message)
            if name in ('fused', 'map', 'centerline', 'path', 'pose') and not entry['frame']:
                entry['error'] = '消息缺少frame_id'
            if name == 'fused' and entry['values']['无效坐标数']:
                entry['error'] = '包含非有限锥桶坐标（已跳过）'
            if name == 'fused':
                self.latest_fused = message
            if callback:
                callback(message)
        self.subscribe(message_type, topic, receive, qos)

    def diagnostic_snapshot(self, wall_now, ros_now):
        streams = {}
        for name, entry in self.diagnostic_inputs.items():
            received = self.received.get(entry['topic'])
            age = None if received is None else max(0., wall_now - received)
            stamp = entry['stamp_ns']
            sample_age = (ros_now - stamp) / 1e9 if ros_now > 0 and stamp is not None and stamp > 0 else None
            state = '未启用' if not entry['topic'] else '未收到' if age is None else '正常'
            if age is not None:
                if name == 'emergency':
                    state = '末次锁存事件'
                elif age > self.settings['stale_seconds'] or (sample_age is not None and sample_age > self.settings['stale_seconds']):
                    state = '超时（缓存）'
                elif sample_age is not None and sample_age < -0.1:
                    state = '时间戳超前'
                elif entry['error']:
                    state = '无效数据'
                elif name in ('fused', 'map', 'centerline', 'path') and entry['values'] is not None:
                    count_key = {'fused': '锥桶数', 'map': '总数', 'centerline': '航点数', 'path': '航点数'}[name]
                    if entry['values'][count_key] == 0:
                        state = '空结果'
            streams[name] = {**entry, 'state': state, 'age_s': age, 'sample_age_s': sample_age}
        velocity_received = self.received.get(self.velocity_topic)
        streams['velocity'] = {'label': '速度反馈' if self.get_parameter('velocity_type').value == 'odometry' else '华测车体速度',
            'topic': self.velocity_topic,
            'state': '未启用' if not self.velocity_topic else '未收到' if velocity_received is None else
                     '超时（缓存）' if wall_now - velocity_received > self.settings['stale_seconds'] else
                     '无效数据' if self.last_speed is None else '正常',
            'age_s': None if velocity_received is None else wall_now - velocity_received,
            'values': {'速度m/s': self.last_speed, '说明': '车体运动速度，不是轮速'}}
        return streams

    def cone_coordinates(self):
        message = self.latest_fused
        result = {'raw_frame': '', 'vehicle_frame': 'base_link', 'stamp_ns': None,
                  'tf_error': None, 'rows': []}
        if message is None:
            return result
        result.update(raw_frame=message.header.frame_id, stamp_ns=stamp_ns(message))
        transform = None
        try:
            if not message.header.frame_id or not result['stamp_ns']:
                raise ValueError('缺少坐标系或采样时间；禁止使用最新TF替代')
            if message.header.frame_id != 'base_link':
                transform = self.tf_buffer.lookup_transform('base_link', message.header.frame_id,
                                                          Time.from_msg(message.header.stamp))
        except (TransformException, ValueError) as error:
            result['tf_error'] = str(error)
        for index, cone in enumerate(message.cones):
            if not finite_point(cone.position):
                continue
            point = cone.position
            vehicle = None
            if result['tf_error'] is None:
                converted = do_transform_point(PointStamped(header=message.header, point=point), transform).point if transform else point
                if finite_point(converted):
                    vehicle = [converted.x, converted.y, converted.z]
            result['rows'].append({'index': index, 'color': COLORS.get(cone.color, str(cone.color)),
                'confidence': cone.confidence if math.isfinite(cone.confidence) else None,
                'raw': [point.x, point.y, point.z], 'vehicle': vehicle})
        # JSON/Qt numeric values cannot represent epoch nanoseconds exactly.
        result['stamp_ns'] = str(result['stamp_ns'])
        return result

    def marker(self, header, namespace, marker_type, marker_id=0):
        marker = Marker()
        marker.header = deepcopy(header)
        marker.ns, marker.id = namespace, marker_id
        marker.type, marker.action = marker_type, Marker.ADD
        marker.pose.orientation.w = 1.
        marker.color.a = 1.
        # Live observations expire in ROS time; map/path/trajectory persist.
        return marker

    def cleared(self, header):
        clear = Marker()
        clear.header = deepcopy(header)
        clear.action = Marker.DELETEALL
        return MarkerArray(markers=[clear])

    def cones(self, message, publisher, namespace):
        if not message.header.frame_id:
            self.get_logger().warning(f'{namespace}: empty frame_id; message dropped')
            return
        array = self.cleared(message.header)
        colors = {0: (1., 0., 0.), 1: (1., .9, 0.), 2: (0., .4, 1.), 3: (1., .5, 0.)}
        for index, cone in enumerate(map_cones(message) if namespace == 'cone_map' else message.cones):
            if not finite_point(cone.position):
                continue
            marker = self.marker(message.header, namespace, Marker.CYLINDER, index)
            marker.pose.position = deepcopy(cone.position)
            marker.scale.x = marker.scale.y = .25
            marker.scale.z = .4
            marker.color.r, marker.color.g, marker.color.b = colors.get(cone.color, (1., 1., 1.))
            if namespace == 'fused_cones':
                marker.lifetime.sec = 1
            array.markers.append(marker)
        publisher.publish(array)

    def lane(self, message, publisher, namespace, color):
        if not message.header.frame_id:
            self.get_logger().warning(f'{namespace}: empty frame_id; message dropped')
            return
        array = self.cleared(message.header)
        marker = self.marker(message.header, namespace, Marker.LINE_STRIP)
        marker.scale.x = .09
        marker.color.r, marker.color.g, marker.color.b = color
        for waypoint in message.waypoints:
            frame = waypoint.pose.header.frame_id
            if frame and frame != message.header.frame_id:
                self.get_logger().warning(f'{namespace}: waypoint frame mismatch; display cleared')
                publisher.publish(array)
                return
            if finite_point(waypoint.pose.pose.position):
                marker.points.append(deepcopy(waypoint.pose.pose.position))
        if len(marker.points) >= 2:
            array.markers.append(marker)
        publisher.publish(array)

    def pose(self, message):
        point = message.pose.position
        if not message.header.frame_id or not finite_point(point):
            return
        stamp = message.header.stamp.sec * 10**9 + message.header.stamp.nanosec
        reset = self.pose_frame != message.header.frame_id or (self.pose_stamp is not None and stamp < self.pose_stamp)
        if self.points and math.dist((point.x, point.y, point.z),
                                    (self.points[-1].x, self.points[-1].y, self.points[-1].z)) > self.settings['trajectory_max_step']:
            reset = True
        if reset:
            self.points.clear()
        self.pose_frame, self.pose_stamp = message.header.frame_id, stamp
        self.last_pose_received = time.monotonic()
        if not self.points or math.dist((point.x, point.y, point.z),
                                       (self.points[-1].x, self.points[-1].y, self.points[-1].z)) >= self.settings['trajectory_min_distance']:
            self.points.append(deepcopy(point))
        array = self.cleared(message.header)
        if len(self.points) >= 2:
            marker = self.marker(message.header, 'localization_trajectory', Marker.LINE_STRIP)
            marker.points = list(self.points)
            marker.scale.x = .06
            marker.color.r, marker.color.g, marker.color.b = 1., .85, 0.
            array.markers.append(marker)
        self.trajectory_pub.publish(array)

    def speed(self, message):
        velocity = message.twist.linear if isinstance(message, TwistStamped) else message.twist.twist.linear
        speed = math.hypot(velocity.x, velocity.y)
        if math.isfinite(speed):
            self.last_speed, self.last_received = speed, time.monotonic()
        else:
            self.last_speed, self.last_received = None, None

    def render_speed(self):
        now = time.monotonic()
        ros_time = self.get_clock().now()
        if ros_time.nanoseconds < self.last_ros_time:
            self.points.clear()
            self.pose_stamp = None
            self.last_speed, self.last_received = None, None
            if self.bridge_mode and self.pose_frame:
                from std_msgs.msg import Header
                self.trajectory_pub.publish(self.cleared(Header(frame_id=self.pose_frame)))
        self.last_ros_time = ros_time.nanoseconds
        marker = Marker()
        # Follow the same real TF frame as the RViz camera; never publish a fake TF.
        marker.header.frame_id = self.settings['target_frame']
        marker.header.stamp = ros_time.to_msg()
        marker.ns, marker.id = 'fsd_velocity', 0
        marker.type, marker.action = Marker.TEXT_VIEW_FACING, Marker.ADD
        marker.pose.orientation.w = 1.
        marker.pose.position.z = 2.5
        marker.frame_locked = True
        marker.scale.z = .5
        marker.color.a = 1.
        stale = self.last_received is None or now - self.last_received > self.settings['stale_seconds']
        if stale:
            marker.text = 'FSD speed: -- (STALE)'
            marker.color.r, marker.color.g = 1., .4
        else:
            marker.text = f'FSD speed: {self.last_speed:.2f} m/s | {self.last_speed * 3.6:.1f} km/h'
            marker.color.g, marker.color.b = 1., .8
        self.speed_pub.publish(marker)
        self.status_pub.publish(String(data=json.dumps({
            'schema_version': 1, 'stale_seconds': self.settings['stale_seconds'],
            'streams': self.diagnostic_snapshot(now, ros_time.nanoseconds),
            'cone_coordinates': self.cone_coordinates(),
            'speed_source': self.velocity_topic, 'speed_stale': stale,
            'speed_m_s': None if stale else self.last_speed,
            'input_wall_age_s': {topic: None if received is None else now - received
                                 for topic, received in self.received.items()},
            'trajectory_points': len(self.points), 'bridge_mode': bool(self.bridge_mode),
        }, allow_nan=False)))
