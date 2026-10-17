"""Check truth-preserving display states and sensor-time coordinate conversion."""
import json
import math
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from geometry_msgs.msg import TransformStamped
from std_msgs.msg import String, Bool
from tf2_ros import TransformBroadcaster
from wuta_msgs.msg import Cone, ConeArray, ConeMap, MissionState
from autoware_msgs.msg import Command, Lane
from fsd_visualization.bridge import DisplayBridge
from fsd_visualization.diagnostics import summary
from test_interfaces import spin_until

CONFIG = Path(__file__).resolve().parents[1] / 'src/fsd_visualization/config/topics.yaml'


def test_diagnostics_empty_cached_latched_and_stamped_tf():
    rclpy.init(args=['--ros-args', '-p', f'topics_config:={CONFIG}', '-p', 'bridge_mode:=true'])
    probe = Node('isolated_diagnostics_probe', use_global_arguments=False)
    latched_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                            durability=DurabilityPolicy.TRANSIENT_LOCAL)
    latch = probe.create_publisher(Bool, '/planning/global_centerline_ready', latched_qos)
    latch.publish(Bool(data=True))
    emergency_event = probe.create_publisher(Bool, '/system/emergency', latched_qos)
    emergency_event.publish(Bool(data=True))
    bridge = DisplayBridge()  # Join after both one-shot latched publications.
    observed = []
    subscription = probe.create_subscription(String, '/fsd_visualization/status',
        lambda msg: observed.append(json.loads(msg.data)), bridge.qos)
    publishers = {name: probe.create_publisher(typ, bridge.topics[key], bridge.qos)
                  for name, key, typ in [('fused', 'fused_cones', ConeArray), ('map', 'cone_map', ConeMap),
                    ('mission', 'mission', MissionState), ('command', 'command', Command),
                    ('path', 'final_waypoints', Lane), ('fusion_status', 'fusion_status', String)]}
    broadcaster = TransformBroadcaster(probe)
    nodes = [bridge, probe]
    try:
        spin_until(nodes, lambda: all(pub.get_subscription_count() for pub in publishers.values()) and observed)
        assert observed[-1]['streams']['fused']['state'] == '未收到'
        assert observed[-1]['streams']['path']['state'] == '未收到'
        empty = ConeArray()
        empty.header.frame_id = 'test_lidar'
        empty.header.stamp = probe.get_clock().now().to_msg()
        publishers['fused'].publish(empty)
        publishers['map'].publish(ConeMap(header=empty.header))
        spin_until(nodes, lambda: observed[-1]['streams']['fused']['state'] == '空结果' and
                   observed[-1]['streams']['map']['state'] == '空结果')
        assert observed[-1]['cone_coordinates']['rows'] == []
        mission = MissionState(state=MissionState.EXPLORE, mission_mode=MissionState.MISSION_TRACKDRIVE,
            description='mode_selected=1 go_count=1 go_pending=0 lap=0 wait=none')
        mission.header.stamp = empty.header.stamp
        publishers['mission'].publish(mission)
        publishers['command'].publish(Command(speed=3., angle=2., throttle_brake=.2))
        publishers['fusion_status'].publish(String(data='broken json'))
        spin_until(nodes, lambda: observed[-1]['streams']['fusion_status']['state'] == '无效数据' and
                   observed[-1]['streams']['command']['values'] is not None)
        assert observed[-1]['streams']['command']['values']['指令语义'] == '正驱动请求'
        assert observed[-1]['streams']['mission']['values']['阶段'] == 'EXPLORE'
        assert observed[-1]['streams']['mission']['values']['wait'] == 'none'
        # A continuously received but old stamped command is still stale.
        command = Command(speed=3., throttle_brake=.2)
        command.header.stamp = probe.get_clock().now().to_msg()
        command.header.stamp.sec -= 5
        publishers['command'].publish(command)
        spin_until(nodes, lambda: observed[-1]['streams']['command']['state'] == '超时（缓存）')
        assert observed[-1]['streams']['command']['age_s'] < .5
        assert observed[-1]['streams']['command']['sample_age_s'] > 4
        cone = Cone(color=Cone.COLOR_BLUE, confidence=.8)
        cone.position.x, cone.position.y = 2., 1.
        empty.cones = [cone]
        # Query strictly between two TF samples: interpolation must be used,
        # rather than the latest transform (which would produce X=5).
        start_ns = probe.get_clock().now().nanoseconds
        empty.header.stamp.sec = start_ns // 10**9
        empty.header.stamp.nanosec = start_ns % 10**9
        transforms = []
        for offset_ns, x in [(-100_000_000, 1.), (100_000_000, 3.)]:
            transform = TransformStamped()
            transform.header.frame_id, transform.child_frame_id = 'base_link', 'test_lidar'
            timestamp = start_ns + offset_ns
            transform.header.stamp.sec, transform.header.stamp.nanosec = divmod(timestamp, 10**9)
            transform.transform.translation.x = x
            transform.transform.rotation.w = 1.
            transforms.append(transform)
        # Ensure the listener is discovered before publishing the bounded history.
        spin_until(nodes, lambda: broadcaster.pub_tf.get_subscription_count() > 0)
        broadcaster.sendTransform(transforms)
        publishers['fused'].publish(empty)
        spin_until(nodes, lambda: observed[-1]['cone_coordinates']['rows'] and
                   observed[-1]['cone_coordinates']['rows'][0]['vehicle'] is not None)
        coordinate = observed[-1]['cone_coordinates']['rows'][0]
        assert observed[-1]['cone_coordinates']['stamp_ns'] == str(start_ns)
        assert coordinate['raw'] == [2., 1., 0.]
        assert math.isclose(coordinate['vehicle'][0], 4., abs_tol=1e-6)
        empty.header.frame_id = 'missing_frame'
        publishers['fused'].publish(empty)
        spin_until(nodes, lambda: observed[-1]['cone_coordinates']['raw_frame'] == 'missing_frame')
        assert observed[-1]['cone_coordinates']['tf_error']
        assert observed[-1]['cone_coordinates']['rows'][0]['vehicle'] is None
        assert observed[-1]['cone_coordinates']['rows'][0]['raw'] == [2., 1., 0.]
        # Latched readiness is still available when the viewer joins late.
        spin_until(nodes, lambda: observed[-1]['streams']['global_ready']['values'] == {'值': True})
        spin_until(nodes, lambda: observed[-1]['streams']['command']['state'] == '超时（缓存）')
        assert observed[-1]['streams']['command']['values']['开度'] == .2
        spin_until(nodes, lambda: observed[-1]['streams']['emergency']['age_s'] is not None and
                   observed[-1]['streams']['emergency']['age_s'] > 1)
        assert observed[-1]['streams']['emergency']['state'] == '末次锁存事件'
        assert observed[-1]['streams']['emergency']['values'] == {'值': True}
        # Every owned output is display-only, even with real command inputs.
        topics = probe.get_publisher_names_and_types_by_node(bridge.get_name(), bridge.get_namespace())
        assert all(topic.startswith('/fsd_visualization/') or topic in ('/rosout', '/parameter_events')
                   for topic, _ in topics)
    finally:
        bridge.destroy_node()
        probe.destroy_node()
        rclpy.try_shutdown()


def test_disabled_and_invalid_coordinates(tmp_path):
    import yaml
    settings = yaml.safe_load(CONFIG.read_text())
    for key in ('mission', 'command', 'cone_map', 'centerline', 'final_waypoints'):
        settings['topics'][key] = ''
    config = tmp_path / 'sensor.yaml'
    config.write_text(yaml.safe_dump(settings))
    rclpy.init(args=['--ros-args', '-p', f'topics_config:={config}'])
    bridge = DisplayBridge()
    try:
        snapshot = bridge.diagnostic_snapshot(time.monotonic(), 0)
        assert snapshot['command']['state'] == '未启用'
        assert snapshot['map']['state'] == '未启用'
        assert snapshot['fused']['state'] == '未收到'
        cone = Cone()
        cone.position.x = math.nan
        message = ConeArray(cones=[cone])
        bridge.latest_fused = message
        assert summary('fused', message)['无效坐标数'] == 1
        assert bridge.cone_coordinates()['rows'] == []
        assert bridge.cone_coordinates()['tf_error']
        cone.position.x = 1.
        message.header.frame_id = 'base_link'
        assert bridge.cone_coordinates()['rows'][0]['vehicle'] is None
        assert summary('command', Command(throttle_brake=math.nan))['指令语义'] == '无效值'
    finally:
        bridge.destroy_node()
        rclpy.try_shutdown()
