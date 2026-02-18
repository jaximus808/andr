#include "andr/wander.h"

Wander::Wander(const std::string& name, const BT::NodeConfiguration& config, rclcpp::Node::SharedPtr node_ptr) 
        : BT::StatefulActionNode(name, config), node_(node_ptr)
{    this->wander_client_ = rclcpp_action::create_client<WanderAction>(node_ptr, "wander_planner");
}

BT::PortsList Wander::providedPorts() {
  return {};
}

BT::NodeStatus Wander::onStart() {
    std::cout << "[Idle] Starting to wander..." << std::endl;
    this->madeWanderTask = false; 
    return BT::NodeStatus::RUNNING;
}

BT::NodeStatus Wander::onRunning() { 
        // Print dots to show it's working
    if (!this->madeWanderTask) {
        this->madeWanderTask = this->request_wander(); 
        std::cout << "made request" << std::flush;
    }

    return BT::NodeStatus::RUNNING;
}

void Wander::onHalted() {
    std::cout << "\n[Idle] INTERRUPTED BY HIGHER PRIORITY!" << std::endl;
    std::cout << "[Idle] Stopping wheels." << std::endl;
}

bool Wander::request_wander() {
    
    if (!this->wander_client_->wait_for_action_server(std::chrono::seconds(10))) {
        std::cout << "server not avail" << std::endl;
        return false;
    }

    auto goal_msg = WanderAction::Goal(); 

    auto send_goal_options = rclcpp_action::Client<WanderAction>::SendGoalOptions(); 

    send_goal_options.feedback_callback =
        [this](GoalHandleWander::SharedPtr, const std::shared_ptr<const WanderAction::Feedback> feedback) {
            std::cout << "Currnet prgoress" << feedback->progress_percentage << std::endl;
        };
    send_goal_options.result_callback =
        [this](const GoalHandleWander::WrappedResult & result) {
            switch (result.code) {
                case rclcpp_action::ResultCode::SUCCEEDED:
                    std::cout << "task done!" << std::endl;
                    break;
                case rclcpp_action::ResultCode::ABORTED:
                    std::cout << "task aborted!" << std::endl;
                    break;
                case rclcpp_action::ResultCode::CANCELED:
                    std::cout << "task canceld!" << std::endl;
                    break;
                default:
                    std::cout << "unknlkwn call" << std::endl;
                    break;
            }
            this->madeWanderTask = false; 
        };
    this->wander_client_->async_send_goal(goal_msg, send_goal_options);
    return true;
}
