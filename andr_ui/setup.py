from setuptools import setup, find_packages
import os
from glob import glob

package_name = "andr_ui"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        # Install static assets so the server can find them after install
        (f"share/{package_name}/static", glob("andr_ui/static/*")),
    ],
    install_requires=[
        "setuptools",
        "fastapi",
        "uvicorn[standard]",
        "websockets",
    ],
    entry_points={
        "console_scripts": [
            "ui_server = andr_ui.server:main",
        ],
    },
)
