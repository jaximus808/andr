cmake_minimum_required(VERSION 3.8)
project(andr_sim)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY description launch config worlds
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
