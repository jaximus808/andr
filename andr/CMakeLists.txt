cmake_minimum_required(VERSION 3.8)
project(andr)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# 1. FIND PACKAGES
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(behaviortree_cpp_v3 REQUIRED)
find_package(rclcpp_action REQUIRED) 
find_package(rosidl_default_generators REQUIRED) 
find_package(builtin_interfaces REQUIRED)
find_package(yaml-cpp REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(ament_index_cpp REQUIRED)

# 2. GENERATE ACTION + MESSAGE INTERFACES
rosidl_generate_interfaces(${PROJECT_NAME}
    "msg/Prompt.msg"
    "msg/RobotSpeech.msg"
    "srv/SaveMap.srv"
    "srv/GetMaps.srv"
    "srv/SavePoint.srv"
    "srv/GetMapPoints.srv"
    "srv/GetMapWithPoints.srv"
    "srv/SetSlamConfig.srv"
    "srv/GetSlamConfig.srv"
    "srv/RestartSlam.srv"
    "srv/GetPointCoordinates.srv"
    "srv/RegisterTool.srv"
    "srv/DeregisterTool.srv"
    "srv/ListTools.srv"
    "srv/GetAgentConfig.srv"
    "srv/SetAgentConfig.srv"
    "action/Wander.action"
    "action/Agent.action"
    "action/ExecuteSkill.action"
    "action/TaskGoal.action"
    DEPENDENCIES builtin_interfaces
)

# 3. CREATE LIBRARY (The Brain)
add_library(bt_nodes_lib src/brain.cpp)

target_include_directories(bt_nodes_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

ament_target_dependencies(bt_nodes_lib
  rclcpp
  std_msgs
  behaviortree_cpp_v3
  rclcpp_action
  ament_index_cpp
)

rosidl_get_typesupport_target(cpp_types "${PROJECT_NAME}" "rosidl_typesupport_cpp")
target_link_libraries(bt_nodes_lib "${cpp_types}")

# 4. CREATE EXECUTABLE
# FIX B: Removed 'src/brain.cpp' because it is already in 'bt_nodes_lib'
#
file(GLOB_RECURSE SOURCES "src/*.cpp")

add_executable(andr_brain ${SOURCES})

target_link_libraries(andr_brain
  bt_nodes_lib
  BT::behaviortree_cpp_v3
)

ament_target_dependencies(andr_brain 
  rclcpp 
  std_msgs 
  behaviortree_cpp_v3
  rclcpp_action
  # removed rosidl_default_generators (not needed here)
)

# Connect the executable to the action generation
target_link_libraries(andr_brain "${cpp_types}")
target_link_libraries(andr_brain yaml-cpp)
# 5. INSTALLATION
install(TARGETS bt_nodes_lib
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

# B. Install the EXECUTABLE to the package specific folder
install(TARGETS andr_brain
  DESTINATION lib/${PROJECT_NAME}
)

# C. Install config files
install(FILES
  config/behavior_tree.xml
  DESTINATION share/${PROJECT_NAME}
)

install(DIRECTORY include/
  DESTINATION include
)

install(DIRECTORY launch/
  DESTINATION share/${PROJECT_NAME}/launch
)

ament_package()
