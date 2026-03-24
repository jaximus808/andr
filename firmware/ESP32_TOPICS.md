# ESP32 Sensor Topics for ANDR

The ESP32 publishes sensor data to the Jetson over serial via micro-ROS.
The EKF (`robot_localization`) subscribes to these topics and fuses them
into a single `/odometry/filtered` estimate.

**Do NOT publish the `odom → base_link` TF from the ESP32.**
The EKF handles that transform. Only publish the topics below.

---

## Topic 1: `/wheel/odom`

**Type:** `nav_msgs/msg/Odometry`

Wheel encoder odometry. The ESP32 reads encoder ticks, does the
differential drive math, and publishes accumulated pose + velocity.

### Fields to populate

```
header:
  stamp:              # current time (from micro-ROS synced clock)
  frame_id: "odom"    # the fixed world-ish frame

child_frame_id: "base_link"

pose:
  pose:
    position:
      x:              # accumulated X position (meters)
      y:              # accumulated Y position (meters)
      z: 0.0          # always zero for ground robot
    orientation:
      x: 0.0
      y: 0.0
      z:              # sin(theta/2)  — yaw as quaternion
      w:              # cos(theta/2)
  covariance:         # 6×6 row-major (36 floats), fill diagonal:
                      #   [0]  = var_x     (try 0.01)
                      #   [7]  = var_y     (try 0.01)
                      #   [35] = var_yaw   (try 0.03)
                      #   rest = 0.0 or large (e.g. 1e6 for unused axes)

twist:
  twist:
    linear:
      x:              # forward velocity (m/s) from encoder deltas
      y: 0.0          # always zero for diff drive
      z: 0.0
    angular:
      x: 0.0
      y: 0.0
      z:              # angular velocity (rad/s) from encoder deltas
  covariance:         # 6×6 row-major (36 floats), fill diagonal:
                      #   [0]  = var_vx    (try 0.01)
                      #   [35] = var_vyaw  (try 0.03)
```

### Diff drive math reference

```
dt = current_time - last_time

# Ticks to distance (per wheel)
d_left  = (left_ticks  - prev_left)  * (2π * wheel_radius / ticks_per_rev)
d_right = (right_ticks - prev_right) * (2π * wheel_radius / ticks_per_rev)

# Robot displacement
d_center = (d_left + d_right) / 2.0
d_theta  = (d_right - d_left) / wheel_separation

# Integrate pose
theta += d_theta
x     += d_center * cos(theta)
y     += d_center * sin(theta)

# Velocities
vx    = d_center / dt
vyaw  = d_theta  / dt
```

**Robot constants (from URDF):**

| Parameter | Value |
|---|---|
| `wheel_radius` | 0.05 m |
| `wheel_separation` | 0.34 m |
| `ticks_per_rev` | depends on your encoder |

---

## Topic 2: `/imu/data`

**Type:** `sensor_msgs/msg/Imu`

IMU data — orientation, angular velocity, and linear acceleration.
If using an MPU6050 (no magnetometer), you'll only have gyro + accel.
If using a BNO055 or ICM-20948 (with magnetometer), you get absolute orientation.

### Fields to populate

```
header:
  stamp:              # current time
  frame_id: "imu_link"  # or "base_link" if IMU is at robot center

orientation:          # quaternion (x, y, z, w)
  x:                  # from sensor fusion (DMP/Madgwick/Mahony)
  y:                  # if no orientation filter, set all to 0
  z:                  # and set covariance[0] = -1 to signal "no data"
  w:
orientation_covariance:  # 3×3 row-major (9 floats)
                      #   [0] = var_roll   (try 0.01)
                      #   [4] = var_pitch  (try 0.01)
                      #   [8] = var_yaw    (try 0.01, or 0.05 if gyro-only)
                      #   set [0] = -1 if orientation is not available

angular_velocity:     # rad/s from gyroscope
  x:                  # roll rate
  y:                  # pitch rate
  z:                  # yaw rate
angular_velocity_covariance:  # 3×3 row-major (9 floats)
                      #   [0],[4],[8] diagonal (try 0.001)

linear_acceleration:  # m/s² from accelerometer (includes gravity!)
  x:                  # forward/back
  y:                  # left/right
  z:                  # up/down (~9.81 when stationary)
linear_acceleration_covariance:  # 3×3 row-major (9 floats)
                      #   [0],[4],[8] diagonal (try 0.1)
```

### IMU notes

- **Gravity:** Publish raw accel _including_ gravity. The EKF config has
  `imu0_remove_gravitational_acceleration: true` so it handles subtraction.
- **No magnetometer (MPU6050)?** Set `imu0_relative: true` in `ekf.yaml`
  so the EKF treats yaw as relative (gyro-integrated) rather than absolute.
  Orientation will drift over time — the wheel odom corrects for this.
- **Has magnetometer (BNO055)?** Leave `imu0_relative: false` in `ekf.yaml`.
  You get absolute yaw that doesn't drift.
- **No orientation at all?** Set all orientation fields to 0 and put `-1` in
  `orientation_covariance[0]`. The EKF will skip orientation and only use
  angular velocity + linear acceleration.

---

## Topic 3: `/scan` (not from ESP32)

The LiDAR publishes this directly — just listing it for completeness.
SLAM Toolbox and Nav2 costmaps subscribe to it.

**Type:** `sensor_msgs/msg/LaserScan`

This comes from your LiDAR driver node (e.g., `rplidar_ros` or `sllidar_ros`),
not from the ESP32.

---

## Publish rates

| Topic | Recommended rate | Min viable |
|---|---|---|
| `/wheel/odom` | 50 Hz | 20 Hz |
| `/imu/data` | 100 Hz | 50 Hz |
| `/scan` | 10 Hz | 5 Hz |

The EKF runs at 50 Hz (set in `ekf.yaml`). It interpolates between sensor
updates, so higher rates give smoother output but aren't strictly required.

---

## Frame IDs summary

```
map              ← SLAM Toolbox publishes map→odom TF
 └─ odom         ← EKF publishes odom→base_link TF
     └─ base_link
         ├─ imu_link     ← static TF (from URDF or a static_transform_publisher)
         ├─ lidar_link   ← static TF (from URDF)
         ├─ left_wheel
         └─ right_wheel
```

If your IMU is mounted at the robot center, use `frame_id: "base_link"` in the
IMU message and skip the `imu_link` frame. If it's offset, add an `imu_link`
joint to the URDF so the EKF can transform the readings to `base_link`.

---

## Quick test (without the full stack)

```bash
# Terminal 1: start micro-ROS agent
ros2 run micro_ros_agent micro_ros_agent serial --dev /dev/ttyUSB0 --baud 921600

# Terminal 2: check topics are publishing
ros2 topic list | grep -E "wheel|imu"
ros2 topic hz /wheel/odom
ros2 topic hz /imu/data

# Terminal 3: inspect message content
ros2 topic echo /wheel/odom --once
ros2 topic echo /imu/data --once
```
