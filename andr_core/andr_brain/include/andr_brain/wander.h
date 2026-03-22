#ifndef ANDR_WANDER_H
#define ANDR_WANDER_H
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "andr_msgs/action/wander.hpp"
#include <atomic>
#include <behaviortree_cpp_v3/action_node.h>

class Wander : public BT::StatefulActionNode {
public:
    using WanderAction = andr_msgs::action::Wander;
    using GoalHandleWander = rclcpp_action::ClientGoalHandle<WanderAction>;
    Wander(const std::string& name,
           const BT::NodeConfiguration& config,
           rclcpp::Node::SharedPtr node_ptr);

    static BT::PortsList providedPorts();

    BT::NodeStatus onStart() override;

    BT::NodeStatus onRunning() override;

    void onHalted() override;

private:
    bool madeWanderTask;
    std::atomic<bool> goal_accepted_{false};
    std::atomic<bool> task_finished_{false};
    std::atomic<bool> task_succeeded_{false};
    rclcpp::Node::SharedPtr node_;
    rclcpp_action::Client<WanderAction>::SharedPtr wander_client_;

    bool request_wander();
};


#endif
