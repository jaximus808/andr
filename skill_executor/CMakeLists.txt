cmake_minimum_required(VERSION 3.8)
project(skill_executor)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# ── Dependencies ──────────────────────────────────────────────────────────────
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(andr REQUIRED)          # provides the ExecuteSkill action type
find_package(yaml-cpp REQUIRED)

# ── Executable ────────────────────────────────────────────────────────────────
add_executable(skill_executor_node
  src/skill_executor.cpp
)

target_include_directories(skill_executor_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

ament_target_dependencies(skill_executor_node
  rclcpp
  rclcpp_action
  andr
)

target_link_libraries(skill_executor_node yaml-cpp)

# ── Install ───────────────────────────────────────────────────────────────────
install(TARGETS skill_executor_node
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY include/
  DESTINATION include
)

# ── Tests ─────────────────────────────────────────────────────────────────────
if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
