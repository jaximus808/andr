from setuptools import setup

package_name = "robot_skills"

setup(
    name=package_name,
    version="0.0.0",
    packages=[package_name],
    package_data={package_name: ["migrations/*.sql"]},
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="andr",
    maintainer_email="dev@andr.local",
    description="Mock hardware-interface skill action servers for ANDR",
    license="MIT",
    entry_points={
        "console_scripts": [
            "speak_server = robot_skills.speak_server:main",
            "walk_server  = robot_skills.walk_server:main",
            "spin_server  = robot_skills.spin_server:main",
            "map_server                = robot_skills.map_server:main",
            "navigate_to_point_server  = robot_skills.navigate_to_point_server:main",
            "go_to_point_server        = robot_skills.go_to_point_server:main",
            "vision_server             = robot_skills.vision_server:main",
            "gesture_server            = robot_skills.gesture_server:main",
            "vision_task_bridge        = robot_skills.vision_task_bridge:main",
        ],
    },
)
