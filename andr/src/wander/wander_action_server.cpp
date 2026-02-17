#include "andr/wander/wander_action_server.h"

enum class StateExecute {
    READY,// send plan req here
    SENT_PLAN_REQ, // send execute req 
    SENT_EXECUTE_REQ // done 
};

WanderActionServer::WanderActionServer(const rclcpp::NodeOptions & options)
: Node("wander_action_server", options)
{
    using namespace std::placeholders;

    this->wander_planner = WanderPlanner();  

    this->action_server_ = rclcpp_action::create_server<Wander>(
    this,
    "wander",
    std::bind(&WanderActionServer::handle_goal, this, _1, _2),
    std::bind(&WanderActionServer::handle_cancel, this, _1),
    std::bind(&WanderActionServer::handle_accepted, this, _1));

    RCLCPP_INFO(this->get_logger(), "Wander Action Server initialized.");
}

rclcpp_action::GoalResponse WanderActionServer::handle_goal(
  const rclcpp_action::GoalUUID & uuid,
  std::shared_ptr<const Wander::Goal> goal)
{
  RCLCPP_INFO(this->get_logger(), "Received goal request");
  (void)uuid;
  // Logic: Reject if goal is invalid (e.g., negative duration)
  return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}

rclcpp_action::CancelResponse WanderActionServer::handle_cancel(
  const std::shared_ptr<GoalHandleWander> goal_handle)
{
  RCLCPP_INFO(this->get_logger(), "Received request to cancel goal");
  (void)goal_handle;
  return rclcpp_action::CancelResponse::ACCEPT;
}

void WanderActionServer::handle_accepted(const std::shared_ptr<GoalHandleWander> goal_handle)
{
  // Spin up a separate thread so we don't block the executor
  std::thread{std::bind(&WanderActionServer::execute, this, std::placeholders::_1), goal_handle}.detach();
}

void WanderActionServer::execute(const std::shared_ptr<GoalHandleWander> goal_handle)
{
    RCLCPP_INFO(this->get_logger(), "Executing wander...");

    const auto goal = goal_handle->get_goal();
    auto feedback = std::make_shared<Wander::Feedback>();
    auto result = std::make_shared<Wander::Result>();

    rclcpp::Rate loop_rate(10);
    
    //sub 1
    //1 send plan request, 2 get plan, 3 is send plan to be executed, 4 plan executed and done and end 
    StateExecute state_execute = StateExecute::READY;  
    
    std::future<WanderPlanner::SkillState> selected_skill_future;
    WanderPlanner::SkillState selected_skill; 

    // --- START FILLING IN YOUR ROBOT LOGIC HERE ---
    bool finished = false;
    while (rclcpp::ok() && !finished) {
    // 1. Check if the client wants to cancel
    if (goal_handle->is_canceling()) {
        goal_handle->canceled(result);
        RCLCPP_INFO(this->get_logger(), "Wander canceled.");
        return;
    }

    switch (state_execute) {
        case StateExecute::READY:
            selected_skill_future = std::async(std::launch::async, &WanderPlanner::decide_skill, &wander_planner);
            state_execute = StateExecute::SENT_PLAN_REQ;
            break;
        case StateExecute::SENT_PLAN_REQ:
            if (selected_skill_future.valid() 
                    && selected_skill_future.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready) {
                selected_skill = selected_skill_future.get(); 
                state_execute = StateExecute::SENT_EXECUTE_REQ;
            }
            else {
                feedback->current_state = "thinking";
            }
            break;
        case StateExecute::SENT_EXECUTE_REQ:
            //Later this will wait for the task client to finish, but for now just be like meow
            RCLCPP_INFO(this->get_logger(), "Executing Skill: %s", selected_skill.id );
            finished = true; 
            break;
    }

    // 2. Do "Wandering" (e.g., publish to /cmd_vel)

    // 3. Publish feedback
    goal_handle->publish_feedback(feedback);

    loop_rate.sleep();
    }
    // --- END ROBOT LOGIC ---

    if (rclcpp::ok()) {
    result->success = true;
    goal_handle->succeed(result);
    RCLCPP_INFO(this->get_logger(), "Wander completed successfully.");
    }
}


