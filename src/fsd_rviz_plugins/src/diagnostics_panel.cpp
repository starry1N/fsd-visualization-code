// SPDX-License-Identifier: Apache-2.0
#include <chrono>
#include <cmath>
#include <memory>
#include <mutex>
#include <pluginlib/class_list_macros.hpp>
#include <rviz_common/panel.hpp>
#include <rviz_common/display_context.hpp>
#include <rviz_common/window_manager_interface.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction_iface.hpp>
#include <std_msgs/msg/string.hpp>
#include <QComboBox>
#include <QHeaderView>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QDockWidget>
#include <QMainWindow>
#include <QTableWidget>
#include <QTimer>
#include <QVBoxLayout>

namespace fsd_rviz_plugins
{
// ROS callbacks only store the snapshot. Widgets are updated on the Qt thread.
class DiagnosticsPanel : public rviz_common::Panel
{
  struct Snapshot
  {
    std::mutex mutex;
    QByteArray data;
    std::chrono::steady_clock::time_point received;
  };

public:
  explicit DiagnosticsPanel(QWidget * parent = nullptr) : Panel(parent)
  {
    auto * layout = new QVBoxLayout(this);
    banner_ = new QLabel("等待显示桥 /fsd_visualization/status", this);
    banner_->setWordWrap(true);
    layout->addWidget(banner_);
    streams_ = new QTableWidget(0, 4, this);
    streams_->setObjectName("fsd_streams");
    streams_->setHorizontalHeaderLabels({"数据源", "消息状态", "接收/采样龄(s)", "实际输出"});
    setupTable(streams_);
    streams_->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Stretch);
    layout->addWidget(streams_, 3);
    frame_ = new QComboBox(this);
    frame_->setObjectName("fsd_coordinate_frame");
    frame_->addItems({"原始雷达/消息坐标系", "车体坐标系 base_link"});
    layout->addWidget(frame_);
    coordinates_info_ = new QLabel(this);
    coordinates_info_->setWordWrap(true);
    layout->addWidget(coordinates_info_);
    coordinates_ = new QTableWidget(0, 6, this);
    coordinates_->setObjectName("fsd_cone_coordinates");
    coordinates_->setHorizontalHeaderLabels({"本帧序号", "颜色", "X(m)", "Y(m)", "Z(m)", "置信度"});
    setupTable(coordinates_);
    layout->addWidget(coordinates_, 2);
    auto * note = new QLabel("序号不是跨帧ID；置信度不是位置精度。开度是请求，不代表电机实际转动。", this);
    note->setWordWrap(true);
    layout->addWidget(note);
    connect(frame_, qOverload<int>(&QComboBox::currentIndexChanged), this, [this]() {
      draw();
      Q_EMIT configChanged();
    });
    auto * timer = new QTimer(this);
    connect(timer, &QTimer::timeout, this, [this]() {draw();});
    timer->start(100);
  }

  void onInitialize() override
  {
    QTimer::singleShot(0, this, [this]() {
      auto * window = qobject_cast<QMainWindow *>(getDisplayContext()->getWindowManager()->getParentWindow());
      if (!window) {return;}
      for (auto * dock : window->findChildren<QDockWidget *>()) {
        if (dock->widget() == this || dock->isAncestorOf(this)) {
          dock->setFloating(false);
          window->addDockWidget(Qt::RightDockWidgetArea, dock);
          dock->show();
          window->resizeDocks({dock}, {720}, Qt::Horizontal);
          break;
        }
      }
    });
    const auto abstraction = getDisplayContext()->getRosNodeAbstraction().lock();
    if (!abstraction) {return;}
    const auto state = snapshot_;
    subscription_ = abstraction->get_raw_node()->create_subscription<std_msgs::msg::String>(
      "/fsd_visualization/status", rclcpp::QoS(1).best_effort(),
      [state](std_msgs::msg::String::ConstSharedPtr message) {
        std::lock_guard<std::mutex> lock(state->mutex);
        state->data = QByteArray::fromStdString(message->data);
        state->received = std::chrono::steady_clock::now();
      });
  }

  void load(const rviz_common::Config & config) override
  {
    Panel::load(config);
    int frame = 0;
    config.mapGetInt("CoordinateFrame", &frame);
    frame_->setCurrentIndex(frame == 1 ? 1 : 0);
  }

  void save(rviz_common::Config config) const override
  {
    Panel::save(config);
    config.mapSetValue("CoordinateFrame", frame_->currentIndex());
  }

private:
  static void setupTable(QTableWidget * table)
  {
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    table->verticalHeader()->hide();
    table->horizontalHeader()->setSectionResizeMode(QHeaderView::ResizeToContents);
    table->setSortingEnabled(false);  // Preserve the per-frame source order.
  }

  static QString scalar(const QJsonValue & value)
  {
    if (value.isNull() || value.isUndefined()) {return "--";}
    if (value.isBool()) {return value.toBool() ? "true" : "false";}
    if (value.isDouble()) {
      const double number = value.toDouble();
      return QString::number(number, 'f', std::floor(number) == number ? 0 : 3);
    }
    if (value.isString()) {return value.toString();}
    if (value.isArray()) {
      return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact));
    }
    return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
  }

  static void cell(QTableWidget * table, int row, int column, const QString & text,
    const QColor & color = QColor(Qt::black))
  {
    auto * item = table->item(row, column);
    if (!item) {item = new QTableWidgetItem; table->setItem(row, column, item);}
    item->setText(text);
    item->setToolTip(text);
    item->setForeground(color);
  }

  void draw()
  {
    QByteArray data;
    double bridge_age;
    {
      std::lock_guard<std::mutex> lock(snapshot_->mutex);
      data = snapshot_->data;
      bridge_age = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - snapshot_->received).count();
    }
    if (data.isEmpty()) {return;}
    const auto document = QJsonDocument::fromJson(data);
    const auto root = document.object();
    if (document.isNull() || root.value("schema_version").toInt() != 1) {
      banner_->setText("显示桥数据格式无效；当前表格不代表新鲜状态");
      streams_->setRowCount(0);
      coordinates_->setRowCount(0);
      coordinates_info_->clear();
      return;
    }
    const bool bridge_stale = bridge_age > 2.0;
    banner_->setText(bridge_stale ? "显示桥超时！下方全部为缓存，不能判定当前状态" :
      QString("只读诊断｜输入显示超时阈值 %1 s（不是驾驶门控）").arg(root.value("stale_seconds").toDouble()));
    banner_->setStyleSheet(bridge_stale ? "color:#c03030;font-weight:bold;" : "");
    const auto inputs = root.value("streams").toObject();
    const QStringList order = {"mission", "fused", "fusion_status", "map", "centerline", "path",
      "command", "pose", "velocity", "inspection", "localization_ready", "localization_confidence",
      "global_ready", "path_confidence", "emergency"};
    streams_->setRowCount(order.size());
    int row = 0;
    for (const auto & name : order) {
      const auto input = inputs.value(name).toObject();
      auto state = input.value("state").toString("未收到");
      if (bridge_stale) {state = "显示桥超时（缓存）";}
      const bool good = state == "正常" || state == "空结果";
      cell(streams_, row, 0, input.value("label").toString(name));
      streams_->item(row, 0)->setToolTip(input.value("topic").toString());
      cell(streams_, row, 1, state, good ? QColor(0, 110, 70) : QColor(180, 70, 20));
      cell(streams_, row, 2, scalar(input.value("age_s")) + " / " + scalar(input.value("sample_age_s")));
      const auto values = input.value("values").toObject();
      QStringList details;
      QStringList priority;
      if (name == "mission") {
        priority = QStringList{"阶段", "赛项字段", "mode_selected", "go_pending", "wait", "lap", "go_count", "gnss_status", "定位模式"};
      } else if (name == "command") {
        priority = QStringList{"指令语义", "开度", "目标速度m/s", "转角deg"};
      } else if (name == "map") {
        priority = QStringList{"总数", "闭合", "红", "蓝", "黄", "橙", "未知"};
      } else if (name == "fused") {
        priority = QStringList{"锥桶数", "无效坐标数"};
      } else if (name == "fusion_status") {
        priority = QStringList{"reason", "published_cones", "lidar_cones", "matches", "guided_clusters", "camera_delta_ms", "end_to_end_ms"};
      } else if (name == "path" || name == "centerline") {
        priority = QStringList{"航点数", "最小目标速度m/s", "最大目标速度m/s"};
      }
      for (const auto & key : priority) {
        if (values.contains(key)) {details << key + "=" + scalar(values.value(key));}
      }
      for (auto it = values.begin(); it != values.end(); ++it) {
        if (it.key() != "权威诊断" && !priority.contains(it.key())) {
          details << it.key() + "=" + scalar(it.value());
        }
      }
      const auto error = input.value("error").toString();
      if (!error.isEmpty()) {details << error;}
      const bool drive_request = name == "command" && values.value("开度").toDouble() > 0;
      cell(streams_, row, 3, details.join(" | "), drive_request ? QColor(180, 30, 30) : QColor(Qt::black));
      streams_->item(row, 3)->setToolTip(details.join("\n") + "\n" + values.value("权威诊断").toString());
      ++row;
    }
    const auto cones = root.value("cone_coordinates").toObject();
    const bool vehicle = frame_->currentIndex() == 1;
    const auto rows = cones.value("rows").toArray();
    const auto fused_state = inputs.value("fused").toObject().value("state").toString();
    const auto tf_error = cones.value("tf_error").toString();
    QString info = QString("%1｜采样ns=%2｜%3｜%4个有效坐标")
      .arg(vehicle ? "base_link（X前/Y左/Z上）" : cones.value("raw_frame").toString())
      .arg(scalar(cones.value("stamp_ns")))
      .arg(bridge_stale ? "缓存：显示桥超时" : fused_state).arg(rows.size());
    if (vehicle && !tf_error.isEmpty()) {info += "\nTF不可用：" + tf_error;}
    coordinates_info_->setText(info);
    coordinates_->setRowCount(rows.size());
    row = 0;
    for (const auto & value : rows) {
      const auto cone = value.toObject();
      const auto position = cone.value(vehicle ? "vehicle" : "raw").toArray();
      cell(coordinates_, row, 0, QString::number(cone.value("index").toInt()));
      cell(coordinates_, row, 1, cone.value("color").toString());
      for (int axis = 0; axis < 3; ++axis) {
        cell(coordinates_, row, axis + 2, position.size() == 3 ? QString::number(position.at(axis).toDouble(), 'f', 3) : "--");
      }
      cell(coordinates_, row, 5, scalar(cone.value("confidence")));
      ++row;
    }
  }

  std::shared_ptr<Snapshot> snapshot_ = std::make_shared<Snapshot>();
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr subscription_;
  QLabel * banner_;
  QLabel * coordinates_info_;
  QComboBox * frame_;
  QTableWidget * streams_;
  QTableWidget * coordinates_;
};
}  // namespace fsd_rviz_plugins
PLUGINLIB_EXPORT_CLASS(fsd_rviz_plugins::DiagnosticsPanel, rviz_common::Panel)
