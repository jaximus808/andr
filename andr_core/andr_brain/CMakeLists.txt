cmake_minimum_required(VERSION 3.8)
project(andr_brain)

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
find_package(andr_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(yaml-cpp REQUIRED)
find_package(ament_index_cpp REQUIRED)

# 2. CREATE LIBRARY (The Brain)
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
  andr_msgs
)

# 3. CREATE EXECUTABLE
file(GLOB_RECURSE SOURCES "src/*.cpp")

add_executable(andr_brain_node ${SOURCES})

target_include_directories(andr_brain_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

target_link_libraries(andr_brain_node
  bt_nodes_lib
  BT::behaviortree_cpp_v3
  yaml-cpp
)

ament_target_dependencies(andr_brain_node
  rclcpp
  std_msgs
  behaviortree_cpp_v3
  rclcpp_action
  andr_msgs
)

# 4. INSTALLATION
install(TARGETS bt_nodes_lib
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

install(TARGETS andr_brain_node
  DESTINATION lib/${PROJECT_NAME}
)

install(FILES
  config/behavior_tree.xml
  DESTINATION share/${PROJECT_NAME}
)

install(DIRECTORY include/
  DESTINATION include
)

install(DIRECTORY config/
  DESTINATION share/${PROJECT_NAME}/config
)

ament_package()
