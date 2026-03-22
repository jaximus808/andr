from setuptools import setup

package_name = "andr_skills"

setup(
    name=package_name,
    version="0.0.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="andr",
    maintainer_email="jaximus808@gmail.com",
    description="Non-navigation skill tools for ANDR",
    license="MIT",
    entry_points={
        "console_scripts": [
            "speak_server = andr_skills.speak_server:main",
            "gesture_server = andr_skills.gesture_server:main",
            "vision_server = andr_skills.vision_server:main",
            "vision_task_bridge = andr_skills.vision_task_bridge:main",
        ],
    },
)
