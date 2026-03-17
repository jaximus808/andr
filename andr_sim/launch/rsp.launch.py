import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, Command
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time")

    pkg_share = get_package_share_directory("andr_sim")
    xacro_file = os.path.join(pkg_share, "description", "robot.urdf.xacro")

    robot_description = Command(["xacro ", xacro_file])

    return LaunchDescription([
        DeclareLaunchArgument(
            "use_sim_time", default_value="true",
            description="Use simulation clock",
        ),

        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{
                "robot_description": robot_description,
                "use_sim_time": use_sim_time,
            }],
            output="screen",
        ),
    ])
