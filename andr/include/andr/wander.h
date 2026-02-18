#ifndef ANDR_WANDER_H
#define ANDR_WANDER_H
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "andr/action/wander.hpp"
#include <behaviortree_cpp_v3/action_node.h>

class Wander : public BT::StatefulActionNode {
public:
    using WanderAction = andr::action::Wander;
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
    rclcpp::Node::SharedPtr node_;
    rclcpp_action::Client<WanderAction>::SharedPtr wander_client_;

    bool request_wander();
};


#endif
