"""ANDR — SDK for building tools and input sources for the ANDR agent framework.

Requires: ros-humble-ros-base (``sudo apt install ros-humble-ros-base``)

Usage::

    from andr import BaseAgentTool, BaseInputSource

Then subclass and run with ``python my_tool.py``. No colcon workspace needed.
"""

from andr._setup_msgs import setup as _setup_msgs
_setup_msgs()

from andr.tools.base_agent_tool import BaseAgentTool
from andr.tools.base_input_source import BaseInputSource
from andr._version import __version__

__all__ = ["BaseAgentTool", "BaseInputSource", "__version__"]
