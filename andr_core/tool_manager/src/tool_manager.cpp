#include "tool_manager/tool_manager.h"

#include <chrono>
#include <functional>
#include <memory>
#include <string>

using namespace std::placeholders;
using namespace std::chrono_literals;

namespace tool_manager
{

// ── Constructor ──────────────────────────────────────────────────────────────

ToolManagerNode::ToolManagerNode(const rclcpp::NodeOptions & options)
: Node("tool_manager", options)
{
  // ── Service servers ────────────────────────────────────────────────────
  register_srv_ = this->create_service<RegisterTool>(
    "tool_manager/register",
    std::bind(&ToolManagerNode::handle_register, this, _1, _2)
  );

  deregister_srv_ = this->create_service<DeregisterTool>(
    "tool_manager/deregister",
    std::bind(&ToolManagerNode::handle_deregister, this, _1, _2)
  );

  list_srv_ = this->create_service<ListTools>(
    "tool_manager/list",
    std::bind(&ToolManagerNode::handle_list, this, _1, _2)
  );

  // ── Action server (execute) ────────────────────────────────────────────
  action_server_ = rclcpp_action::create_server<ExecuteSkill>(
    this,
    "tool_manager/execute",
    std::bind(&ToolManagerNode::handle_goal,     this, _1, _2),
    std::bind(&ToolManagerNode::handle_cancel,   this, _1),
    std::bind(&ToolManagerNode::handle_accepted, this, _1)
  );

  RCLCPP_INFO(get_logger(), "ToolManagerNode ready — waiting for tool registrations.");
}

// ── RegisterTool service ─────────────────────────────────────────────────────

void ToolManagerNode::handle_register(
  const std::shared_ptr<RegisterTool::Request> req,
  std::shared_ptr<RegisterTool::Response> res)
{
  std::lock_guard<std::mutex> lock(tool_map_mutex_);

  if (tool_map_.find(req->tool_name) != tool_map_.end()) {
    RCLCPP_WARN(get_logger(),
      "Tool '%s' already registered — updating entry.", req->tool_name.c_str());
    // Destroy old action client before replacing
    tool_map_.erase(req->tool_name);
  }

  // Create an action client for this tool's action server
  auto client = rclcpp_action::create_client<ExecuteSkill>(this, req->action_server);

  ToolEntry entry;
  entry.tool_name       = req->tool_name;
  entry.description     = req->description;
  entry.action_server   = req->action_server;
  entry.parameters_json = req->parameters_json;
  entry.category        = req->category;
  entry.tags            = req->tags;
  entry.client          = client;

  tool_map_[req->tool_name] = std::move(entry);

  RCLCPP_INFO(get_logger(),
    "Registered tool '%s' -> '%s'", req->tool_name.c_str(), req->action_server.c_str());

  res->success = true;
  res->message = "Tool '" + req->tool_name + "' registered.";
}

// ── DeregisterTool service ───────────────────────────────────────────────────

void ToolManagerNode::handle_deregister(
  const std::shared_ptr<DeregisterTool::Request> req,
  std::shared_ptr<DeregisterTool::Response> res)
{
  std::lock_guard<std::mutex> lock(tool_map_mutex_);

  auto it = tool_map_.find(req->tool_name);
  if (it == tool_map_.end()) {
    RCLCPP_WARN(get_logger(),
      "Deregister: tool '%s' not found.", req->tool_name.c_str());
    res->success = false;
    res->message = "Tool '" + req->tool_name + "' not found.";
    return;
  }

  tool_map_.erase(it);
  RCLCPP_INFO(get_logger(), "Deregistered tool '%s'.", req->tool_name.c_str());

  res->success = true;
  res->message = "Tool '" + req->tool_name + "' deregistered.";
}

// ── ListTools service ────────────────────────────────────────────────────────

void ToolManagerNode::handle_list(
  const std::shared_ptr<ListTools::Request> /*req*/,
  std::shared_ptr<ListTools::Response> res)
{
  std::lock_guard<std::mutex> lock(tool_map_mutex_);

  for (const auto & [name, entry] : tool_map_) {
    res->tool_names.push_back(entry.tool_name);
    res->descriptions.push_back(entry.description);
    res->action_servers.push_back(entry.action_server);
    res->parameters_json.push_back(entry.parameters_json);
    res->categories.push_back(entry.category);
  }

  RCLCPP_DEBUG(get_logger(), "ListTools: returning %zu tool(s).", tool_map_.size());
}

// ── Action server: handle_goal ───────────────────────────────────────────────

rclcpp_action::GoalResponse ToolManagerNode::handle_goal(
  const rclcpp_action::GoalUUID & /*uuid*/,
  std::shared_ptr<const ExecuteSkill::Goal> goal)
{
  RCLCPP_INFO(get_logger(), "Execute goal: skill='%s'", goal->skill_name.c_str());

  std::lock_guard<std::mutex> lock(tool_map_mutex_);
  if (tool_map_.find(goal->skill_name) == tool_map_.end()) {
    RCLCPP_WARN(get_logger(), "Rejecting unknown tool '%s'", goal->skill_name.c_str());
    return rclcpp_action::GoalResponse::REJECT;
  }

  return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}

// ── Action server: handle_cancel ─────────────────────────────────────────────

rclcpp_action::CancelResponse ToolManagerNode::handle_cancel(
  std::shared_ptr<GoalHandle> /*goal_handle*/)
{
  RCLCPP_INFO(get_logger(), "Cancel requested");
  return rclcpp_action::CancelResponse::ACCEPT;
}

// ── Action server: handle_accepted ───────────────────────────────────────────

void ToolManagerNode::handle_accepted(std::shared_ptr<GoalHandle> goal_handle)
{
  std::thread{std::bind(&ToolManagerNode::execute, this, _1), goal_handle}.detach();
}

// ── Action server: execute ───────────────────────────────────────────────────

void ToolManagerNode::execute(std::shared_ptr<GoalHandle> goal_handle)
{
  const auto goal = goal_handle->get_goal();
  const std::string & skill_name = goal->skill_name;

  RCLCPP_INFO(get_logger(), "Dispatching tool='%s' params='%s'",
    skill_name.c_str(), goal->params_json.c_str());

  auto result = std::make_shared<ExecuteSkill::Result>();

  // Look up the tool entry (take a snapshot under lock)
  rclcpp_action::Client<ExecuteSkill>::SharedPtr client;
  std::string action_server_name;
  {
    std::lock_guard<std::mutex> lock(tool_map_mutex_);
    auto it = tool_map_.find(skill_name);
    if (it == tool_map_.end()) {
      result->success = false;
      result->error_message = "Unknown tool: " + skill_name;
      goal_handle->abort(result);
      return;
    }
    client = it->second.client;
    action_server_name = it->second.action_server;
  }

  // Publish initial feedback
  auto feedback = std::make_shared<ExecuteSkill::Feedback>();
  feedback->status = "dispatching to " + action_server_name;
  feedback->progress = 0.0f;
  goal_handle->publish_feedback(feedback);

  // Wait for the downstream action server
  if (!client->wait_for_action_server(5s)) {
    result->success = false;
    result->error_message =
      "Action server '" + action_server_name + "' not available for tool '" + skill_name + "'";
    RCLCPP_ERROR(get_logger(), "%s", result->error_message.c_str());
    goal_handle->abort(result);
    return;
  }

  // Build and send the downstream goal
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

  auto goal_future = client->async_send_goal(downstream_goal, send_options);

  if (goal_future.wait_for(10s) != std::future_status::ready)
  {
    result->success = false;
    result->error_message = "Failed to send goal to '" + action_server_name + "'";
    goal_handle->abort(result);
    return;
  }

  auto downstream_handle = goal_future.get();
  if (!downstream_handle) {
    result->success = false;
    result->error_message = "Goal rejected by '" + action_server_name + "'";
    goal_handle->abort(result);
    return;
  }

  auto result_future = client->async_get_result(downstream_handle);
  if (result_future.wait_for(60s) != std::future_status::ready)
  {
    result->success = false;
    result->error_message = "Timed out waiting for result from '" + action_server_name + "'";
    goal_handle->abort(result);
    return;
  }

  auto wrapped = result_future.get();

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
    RCLCPP_INFO(get_logger(), "Tool '%s' succeeded: %s",
      skill_name.c_str(), result->result_json.c_str());
  } else {
    goal_handle->abort(result);
    RCLCPP_WARN(get_logger(), "Tool '%s' failed: %s",
      skill_name.c_str(), result->error_message.c_str());
  }
}

}  // namespace tool_manager

// ── main ──────────────────────────────────────────────────────────────────────

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<tool_manager::ToolManagerNode>();
  rclcpp::executors::MultiThreadedExecutor executor;
  executor.add_node(node);
  executor.spin();
  rclcpp::shutdown();
  return 0;
}
