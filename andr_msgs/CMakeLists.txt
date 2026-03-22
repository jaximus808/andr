cmake_minimum_required(VERSION 3.8)
project(andr_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)

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
    "srv/GetSystemPrompt.srv"
    "srv/SetSystemPrompt.srv"
    "srv/GetPromptHistory.srv"
    "action/Wander.action"
    "action/Agent.action"
    "action/ExecuteSkill.action"
    "action/TaskGoal.action"
    DEPENDENCIES builtin_interfaces
)

ament_package()
