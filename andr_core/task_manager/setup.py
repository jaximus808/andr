from setuptools import find_packages, setup

package_name = 'task_manager'

setup(
    name='task-manager',
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='observer',
    maintainer_email='jaximus808@gmail.com',
    description='Task manager action server — bridges UI prompts to the agent.',
    license='MIT',
    extras_require={
        'test': ['pytest'],
    },
    entry_points={
        'console_scripts': [
            'task_manager_server = task_manager.task_manager_server:main',
            'task_brain = task_manager.task_brain:main',
        ],
    },
)
