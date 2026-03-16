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
    this->goal_accepted_ = false;
    this->task_finished_ = false;
    this->task_succeeded_ = false;
    return BT::NodeStatus::RUNNING;
}

BT::NodeStatus Wander::onRunning() {
    // Send the goal once
    if (!this->madeWanderTask) {
        this->madeWanderTask = this->request_wander();
        if (this->madeWanderTask) {
            std::cout << "[Idle] Wander goal sent" << std::endl;
        }
    }

    // Check if the action completed
    if (this->task_finished_) {
        return this->task_succeeded_ ? BT::NodeStatus::SUCCESS : BT::NodeStatus::FAILURE;
    }

    return BT::NodeStatus::RUNNING;
}

void Wander::onHalted() {
    std::cout << "\n[Idle] INTERRUPTED BY HIGHER PRIORITY!" << std::endl;
    std::cout << "[Idle] Stopping wheels." << std::endl;
}

bool Wander::request_wander() {

    if (!this->wander_client_->wait_for_action_server(std::chrono::seconds(10))) {
        std::cout << "[Idle] Wander action server not available" << std::endl;
        return false;
    }

    auto goal_msg = WanderAction::Goal();

    auto send_goal_options = rclcpp_action::Client<WanderAction>::SendGoalOptions();

    send_goal_options.goal_response_callback =
        [this](const GoalHandleWander::SharedPtr & goal_handle) {
            if (!goal_handle) {
                std::cout << "[Idle] Wander goal was rejected by server" << std::endl;
                this->task_finished_ = true;
                this->task_succeeded_ = false;
                return;
            }
            this->goal_accepted_ = true;
        };
    send_goal_options.feedback_callback =
        [](GoalHandleWander::SharedPtr, const std::shared_ptr<const WanderAction::Feedback> feedback) {
            std::cout << "Current progress: " << feedback->progress_percentage << std::endl;
        };
    send_goal_options.result_callback =
        [this](const GoalHandleWander::WrappedResult & result) {
            switch (result.code) {
                case rclcpp_action::ResultCode::SUCCEEDED:
                    std::cout << "[Idle] Wander task done!" << std::endl;
                    this->task_succeeded_ = true;
                    break;
                case rclcpp_action::ResultCode::ABORTED:
                    std::cout << "[Idle] Wander task aborted" << std::endl;
                    break;
                case rclcpp_action::ResultCode::CANCELED:
                    std::cout << "[Idle] Wander task canceled" << std::endl;
                    break;
                default:
                    break;
            }
            this->task_finished_ = true;
        };
    this->wander_client_->async_send_goal(goal_msg, send_goal_options);
    return true;
}
