#ifndef WANDER_ACTION_SERVER_HPP_
#define WANDER_ACTION_SERVER_HPP_

#include <memory>
#include <thread>
#include <queue>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "andr/action/wander.hpp"
#include "andr/wander/wander_planner.h"
class WanderActionServer : public rclcpp::Node
{
public:
    using Wander = andr::action::Wander;
    using GoalHandleWander = rclcpp_action::ServerGoalHandle<Wander>;

    explicit WanderActionServer(const rclcpp::NodeOptions & options = rclcpp::NodeOptions());

private:
    rclcpp_action::Server<Wander>::SharedPtr action_server_;

  // Callback to decide whether to accept or reject a goal
    rclcpp_action::GoalResponse handle_goal(
        const rclcpp_action::GoalUUID & uuid,
        std::shared_ptr<const Wander::Goal> goal);

  // Callback to handle requests to stop the action
    rclcpp_action::CancelResponse handle_cancel(
        const std::shared_ptr<GoalHandleWander> goal_handle);

  // Callback to initiate the execution after a goal is accepted
    void handle_accepted(const std::shared_ptr<GoalHandleWander> goal_handle);

  // The actual "work" loop
    void execute(const std::shared_ptr<GoalHandleWander> goal_handle);

    WanderPlanner wander_planner;  

};

#endif  // WANDER_ACTION_SERVER_HPP_
