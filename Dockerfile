# ============================================================================
# ANDR — Docker build
#
# Stages:
#   1. build      — ROS 2 Humble + colcon build (andr_msgs, tool_manager)
#   2. runtime    — Slim image with `pip install andr` + prebuilt binaries
#
# Usage:
#   docker build -t andr .
#   docker run -it andr
#   docker compose up          # full stack with Ollama
# ============================================================================

# ------------------------------------------------------------------
# Stage 1: Build — compile andr_msgs + tool_manager
# ------------------------------------------------------------------
FROM ros:humble-ros-base AS build

ENV DEBIAN_FRONTEND=noninteractive
ENV ROS_WS=/ros2_ws

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-colcon-common-extensions \
    libyaml-cpp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${ROS_WS}

# Build andr_msgs
COPY andr_msgs/ src/andr_msgs/
RUN . /opt/ros/humble/setup.sh && \
    colcon build --packages-select andr_msgs --cmake-args -DCMAKE_BUILD_TYPE=Release

# Build tool_manager (C++)
COPY andr_core/tool_manager/ src/tool_manager/
RUN . /opt/ros/humble/setup.sh && \
    . ${ROS_WS}/install/setup.sh && \
    colcon build --packages-select tool_manager --cmake-args -DCMAKE_BUILD_TYPE=Release

# ------------------------------------------------------------------
# Stage 2: Runtime — install andr pip package with prebuilt binaries
# ------------------------------------------------------------------
FROM ros:humble-ros-base AS runtime

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Copy the pip package source and install it
COPY pip/andr/ /tmp/andr-pkg/

# Sync prebuilt andr_msgs bindings into the pip package
COPY --from=build /ros2_ws/install/andr_msgs/ /tmp/andr_msgs_install/
RUN PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") && \
    MSGS_SRC="/tmp/andr_msgs_install/local/lib/python${PY_VERSION}/dist-packages/andr_msgs" && \
    LIBS_SRC="/tmp/andr_msgs_install/lib" && \
    rm -rf /tmp/andr-pkg/andr/_msgs_bind/andr_msgs && \
    mkdir -p /tmp/andr-pkg/andr/_msgs_bind/andr_msgs && \
    cp -r ${MSGS_SRC}/* /tmp/andr-pkg/andr/_msgs_bind/andr_msgs/ && \
    rm -f /tmp/andr-pkg/andr/_libs/*.so && \
    mkdir -p /tmp/andr-pkg/andr/_libs && \
    cp ${LIBS_SRC}/libandr_msgs*.so /tmp/andr-pkg/andr/_libs/ && \
    touch /tmp/andr-pkg/andr/_libs/__init__.py

# Sync tool_manager binary
COPY --from=build /ros2_ws/install/tool_manager/lib/tool_manager/tool_manager_node /tmp/andr-pkg/andr/bin/tool_manager_node
RUN chmod +x /tmp/andr-pkg/andr/bin/tool_manager_node

# Sync base classes + runtime modules into the pip package
COPY andr_core/andr_tools/andr_tools/base_agent_tool.py /tmp/andr-pkg/andr/tools/base_agent_tool.py
COPY andr_core/andr_tools/andr_tools/base_input_source.py /tmp/andr-pkg/andr/tools/base_input_source.py
COPY andr_core/agent/agent/ /tmp/andr-pkg/andr/runtime/agent/
COPY andr_core/task_manager/task_manager/ /tmp/andr-pkg/andr/runtime/task_manager/
COPY andr_ui/andr_ui/ /tmp/andr-pkg/andr/runtime/andr_ui/
RUN find /tmp/andr-pkg/andr/runtime -name __pycache__ -exec rm -rf {} + 2>/dev/null || true && \
    touch /tmp/andr-pkg/andr/runtime/__init__.py

# Install the andr pip package
RUN pip3 install --no-cache-dir /tmp/andr-pkg/ && \
    rm -rf /tmp/andr-pkg /tmp/andr_msgs_install

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["full"]
