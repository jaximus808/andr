import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_share = get_package_share_directory("andr_sim")
    gazebo_ros_share = get_package_share_directory("gazebo_ros")

    world_file = LaunchConfiguration("world")
    use_sim_time = LaunchConfiguration("use_sim_time")
    localization = LaunchConfiguration("localization")
    map_file = LaunchConfiguration("map_file")

    # --- Robot State Publisher ---
    rsp = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_share, "launch", "rsp.launch.py")
        ),
        launch_arguments={"use_sim_time": use_sim_time}.items(),
    )

    # --- Gazebo server + client ---
    gazebo_params = os.path.join(pkg_share, "config", "gazebo_params.yaml")

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(gazebo_ros_share, "launch", "gazebo.launch.py")
        ),
        launch_arguments={
            "world": world_file,
            "extra_gazebo_args": "--ros-args --params-file " + gazebo_params,
        }.items(),
    )

    # --- Spawn the robot ---
    spawn_entity = Node(
        package="gazebo_ros",
        executable="spawn_entity.py",
        arguments=[
            "-topic", "robot_description",
            "-entity", "andr",
            "-x", "0.0",
            "-y", "0.0",
            "-z", "0.1",
        ],
        output="screen",
    )

    # --- RViz ---
    rviz_config = os.path.join(pkg_share, "config", "sim.rviz")

    rviz = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="screen",
        arguments=["-d", rviz_config],
        parameters=[{"use_sim_time": True}],
    )

    # --- SLAM Toolbox: mapping mode (default) ---
    slam_mapping_params = os.path.join(
        pkg_share, "config", "slam_toolbox_params.yaml"
    )

    slam_mapping = Node(
        package="slam_toolbox",
        executable="async_slam_toolbox_node",
        name="slam_toolbox",
        output="screen",
        parameters=[slam_mapping_params, {"use_sim_time": True}],
        condition=UnlessCondition(localization),
    )

    # --- SLAM Toolbox: localization mode ---
    slam_localization_params = os.path.join(
        pkg_share, "config", "slam_toolbox_localization_params.yaml"
    )

    slam_localization = Node(
        package="slam_toolbox",
        executable="localization_slam_toolbox_node",
        name="slam_toolbox",
        output="screen",
        parameters=[
            slam_localization_params,
            {
                "use_sim_time": True,
                "map_file_name": map_file,
            },
        ],
        condition=IfCondition(localization),
    )

    # --- Map server node ---
    map_server = Node(
        package="robot_skills",
        executable="map_server",
        name="map_server",
        output="screen",
        parameters=[{"use_sim_time": True}],
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "use_sim_time", default_value="true",
            description="Use simulation clock",
        ),
        DeclareLaunchArgument(
            "world",
            default_value=os.path.join(pkg_share, "worlds", "test_world.world"),
            description="Path to Gazebo world file",
        ),
        DeclareLaunchArgument(
            "localization", default_value="false",
            description="Run in localization mode instead of mapping",
        ),
        DeclareLaunchArgument(
            "map_file", default_value="",
            description="Path to serialized map (without extension) for localization mode",
        ),

        rsp,
        gazebo,
        spawn_entity,
        slam_mapping,
        slam_localization,
        map_server,
        rviz,
    ])
