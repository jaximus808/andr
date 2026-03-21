from setuptools import find_packages, setup

package_name = 'agent'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=[
        'setuptools',
        'pyyaml',
        'chromadb',
        'sentence-transformers',
        'langchain',
        'langchain-core',
        'langchain-ollama',
        'langchain-openai',
    ],
    zip_safe=True,
    maintainer='observer',
    maintainer_email='jaximus808@gmail.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'agent_server = agent.agent:main',
            'prompt_manager = agent.prompt_manager:main',
        ],
    },
)
