#ifndef MY_HEADER_H
#define MY_HEADER_H

#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <ament_index_cpp/get_package_share_directory.hpp>
#include "behaviortree_cpp_v3/bt_factory.h"

#include "andr_brain/task.h"
#include "andr_brain/wander.h"
#include "andr_brain/taskcheck.h"

class RobotBrain : public rclcpp::Node {
public:
    BT::Tree tree_;
    BT::Blackboard::Ptr blackboard_;

    // Constructor declaration
    RobotBrain();

    void init();

    void tick();

    void run();

private:
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr subscription_;


    rclcpp::TimerBase::SharedPtr timer_;

    void timer_callback();
};

#endif
