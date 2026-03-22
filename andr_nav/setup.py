from setuptools import setup

package_name = "andr_nav"

setup(
    name=package_name,
    version="0.0.0",
    packages=[package_name],
    package_data={package_name: ["migrations/*.sql"]},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="andr",
    maintainer_email="jaximus808@gmail.com",
    description="Navigation and map management tools for ANDR",
    license="MIT",
    entry_points={
        "console_scripts": [
            "map_server = andr_nav.map_server:main",
            "navigate_to_point_server = andr_nav.navigate_to_point_server:main",
            "walk_server = andr_nav.walk_server:main",
            "spin_server = andr_nav.spin_server:main",
        ],
    },
)
