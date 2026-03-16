#include "skill_executor/skill_executor.h"

#include <chrono>
#include <fstream>
#include <functional>
#include <memory>
#include <sstream>
#include <string>

#include <yaml-cpp/yaml.h>

using namespace std::placeholders;
using namespace std::chrono_literals;

namespace skill_executor
{

// ── Constructor ──────────────────────────────────────────────────────────────

SkillExecutorNode::SkillExecutorNode(const rclcpp::NodeOptions & options)
: Node("skill_executor", options)
{
  // Declare parameter for config path
  this->declare_parameter<std::string>("config_yaml", "");

  std::string config_path =
    this->get_parameter("config_yaml").as_string();

  if (!config_path.empty()) {
    load_config(config_path);
  } else {
    RCLCPP_WARN(get_logger(), "No config_yaml parameter set — no skills registered.");
  }

  action_server_ = rclcpp_action::create_server<ExecuteSkill>(
    this,
    "skill_executor",
    std::bind(&SkillExecutorNode::handle_goal,     this, _1, _2),
    std::bind(&SkillExecutorNode::handle_cancel,   this, _1),
    std::bind(&SkillExecutorNode::handle_accepted, this, _1)
  );

  RCLCPP_INFO(get_logger(),
    "SkillExecutorNode ready — %zu skill(s) registered.", skill_map_.size());
}

// ── Config loading ───────────────────────────────────────────────────────────

void SkillExecutorNode::load_config(const std::string & yaml_path)
{
  RCLCPP_INFO(get_logger(), "Loading skill config from '%s'", yaml_path.c_str());

  YAML::Node config;
  try {
    config = YAML::LoadFile(yaml_path);
  } catch (const std::exception & e) {
    RCLCPP_ERROR(get_logger(), "Failed to load config '%s': %s", yaml_path.c_str(), e.what());
    return;
  }

  if (!config["skills"]) {
    RCLCPP_WARN(get_logger(), "Config has no 'skills' key.");
    return;
  }

  for (auto it = config["skills"].begin(); it != config["skills"].end(); ++it) {
    std::string skill_name = it->first.as<std::string>();
    auto node = it->second;

    std::string action_server_name = node["action_server"].as<std::string>();
    std::string description = node["description"] ? node["description"].as<std::string>() : "";

    // Create an action client for this skill's backing server
    auto client = rclcpp_action::create_client<ExecuteSkill>(this, action_server_name);

    skill_map_[skill_name] = SkillEntry{action_server_name, description, client};

    RCLCPP_INFO(get_logger(), "  Registered skill '%s' -> '%s'",
      skill_name.c_str(), action_server_name.c_str());
  }
}

// ── handle_goal ──────────────────────────────────────────────────────────────

rclcpp_action::GoalResponse SkillExecutorNode::handle_goal(
  const rclcpp_action::GoalUUID & /*uuid*/,
  std::shared_ptr<const ExecuteSkill::Goal> goal)
{
  RCLCPP_INFO(get_logger(), "Received goal: skill='%s'", goal->skill_name.c_str());

  if (skill_map_.find(goal->skill_name) == skill_map_.end()) {
    RCLCPP_WARN(get_logger(), "Rejecting unknown skill '%s'", goal->skill_name.c_str());
    return rclcpp_action::GoalResponse::REJECT;
  }

  return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}

// ── handle_cancel ────────────────────────────────────────────────────────────

rclcpp_action::CancelResponse SkillExecutorNode::handle_cancel(
  std::shared_ptr<GoalHandle> /*goal_handle*/)
{
  RCLCPP_INFO(get_logger(), "Cancel requested");
  return rclcpp_action::CancelResponse::ACCEPT;
}

// ── handle_accepted ──────────────────────────────────────────────────────────

void SkillExecutorNode::handle_accepted(std::shared_ptr<GoalHandle> goal_handle)
{
  std::thread{std::bind(&SkillExecutorNode::execute, this, _1), goal_handle}.detach();
}

// ── execute ──────────────────────────────────────────────────────────────────

void SkillExecutorNode::execute(std::shared_ptr<GoalHandle> goal_handle)
{
  const auto goal = goal_handle->get_goal();
  const std::string & skill_name = goal->skill_name;

  RCLCPP_INFO(get_logger(), "Dispatching skill='%s' params='%s'",
    skill_name.c_str(), goal->params_json.c_str());

  auto result = std::make_shared<ExecuteSkill::Result>();

  // Look up the skill entry
  auto it = skill_map_.find(skill_name);
  if (it == skill_map_.end()) {
    result->success = false;
    result->error_message = "Unknown skill: " + skill_name;
    goal_handle->abort(result);
    return;
  }

  auto & entry = it->second;

  // Publish initial feedback
  auto feedback = std::make_shared<ExecuteSkill::Feedback>();
  feedback->status = "dispatching to " + entry.action_server;
  feedback->progress = 0.0f;
  goal_handle->publish_feedback(feedback);

  // Wait for the downstream action server
  if (!entry.client->wait_for_action_server(5s)) {
    result->success = false;
    result->error_message =
      "Action server '" + entry.action_server + "' not available for skill '" + skill_name + "'";
    RCLCPP_ERROR(get_logger(), "%s", result->error_message.c_str());
    goal_handle->abort(result);
    return;
  }

  // Build and send the downstream goal (same ExecuteSkill type)
  auto downstream_goal = ExecuteSkill::Goal();
  downstream_goal.skill_name  = goal->skill_name;
  downstream_goal.params_json = goal->params_json;

  auto send_options = rclcpp_action::Client<ExecuteSkill>::SendGoalOptions();

  // Forward feedback from downstream to our caller
  send_options.feedback_callback =
    [goal_handle](
      rclcpp_action::ClientGoalHandle<ExecuteSkill>::SharedPtr,
      const std::shared_ptr<const ExecuteSkill::Feedback> downstream_fb)
    {
      auto fb = std::make_shared<ExecuteSkill::Feedback>();
      fb->status   = downstream_fb->status;
      fb->progress = downstream_fb->progress;
      goal_handle->publish_feedback(fb);
    };

  auto goal_future = entry.client->async_send_goal(downstream_goal, send_options);

  // Wait for goal acceptance
  if (rclcpp::spin_until_future_complete(this->get_node_base_interface(), goal_future, 10s)
      != rclcpp::FutureReturnCode::SUCCESS)
  {
    result->success = false;
    result->error_message = "Failed to send goal to '" + entry.action_server + "'";
    goal_handle->abort(result);
    return;
  }

  auto downstream_handle = goal_future.get();
  if (!downstream_handle) {
    result->success = false;
    result->error_message = "Goal rejected by '" + entry.action_server + "'";
    goal_handle->abort(result);
    return;
  }

  // Wait for the result
  auto result_future = entry.client->async_get_result(downstream_handle);
  if (rclcpp::spin_until_future_complete(this->get_node_base_interface(), result_future, 60s)
      != rclcpp::FutureReturnCode::SUCCESS)
  {
    result->success = false;
    result->error_message = "Timed out waiting for result from '" + entry.action_server + "'";
    goal_handle->abort(result);
    return;
  }

  auto wrapped = result_future.get();

  // Check cancellation
  if (goal_handle->is_canceling()) {
    result->success = false;
    result->error_message = "Cancelled";
    goal_handle->canceled(result);
    return;
  }

  // Forward the downstream result
  result->success       = wrapped.result->success;
  result->result_json   = wrapped.result->result_json;
  result->error_message = wrapped.result->error_message;

  if (result->success) {
    goal_handle->succeed(result);
    RCLCPP_INFO(get_logger(), "Skill '%s' succeeded: %s",
      skill_name.c_str(), result->result_json.c_str());
  } else {
    goal_handle->abort(result);
    RCLCPP_WARN(get_logger(), "Skill '%s' failed: %s",
      skill_name.c_str(), result->error_message.c_str());
  }
}

}  // namespace skill_executor

// ── main ──────────────────────────────────────────────────────────────────────

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<skill_executor::SkillExecutorNode>());
  rclcpp::shutdown();
  return 0;
}
