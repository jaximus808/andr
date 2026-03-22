import os
from glob import glob
from setuptools import setup

package_name = "andr_tools"

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
    description="Base classes for ANDR agent tools — self-registering ROS 2 action servers",
    license="MIT",
)
