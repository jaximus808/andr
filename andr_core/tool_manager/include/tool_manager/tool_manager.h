#pragma once

#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include <andr_msgs/action/execute_skill.hpp>
#include <andr_msgs/srv/register_tool.hpp>
#include <andr_msgs/srv/deregister_tool.hpp>
#include <andr_msgs/srv/list_tools.hpp>

namespace tool_manager
{

struct ToolEntry {
  std::string tool_name;
  std::string description;
  std::string action_server;
  std::string parameters_json;
  std::string category;
  std::vector<std::string> tags;
  rclcpp_action::Client<andr_msgs::action::ExecuteSkill>::SharedPtr client;
};

class ToolManagerNode : public rclcpp::Node
{
public:
  using ExecuteSkill   = andr_msgs::action::ExecuteSkill;
  using GoalHandle     = rclcpp_action::ServerGoalHandle<ExecuteSkill>;
  using RegisterTool   = andr_msgs::srv::RegisterTool;
  using DeregisterTool = andr_msgs::srv::DeregisterTool;
  using ListTools      = andr_msgs::srv::ListTools;

  explicit ToolManagerNode(const rclcpp::NodeOptions & options = rclcpp::NodeOptions{});

private:
  // ── Action server (execute) ──────────────────────────────────────────
  rclcpp_action::Server<ExecuteSkill>::SharedPtr action_server_;

  rclcpp_action::GoalResponse handle_goal(
    const rclcpp_action::GoalUUID & uuid,
    std::shared_ptr<const ExecuteSkill::Goal> goal);

  rclcpp_action::CancelResponse handle_cancel(
    std::shared_ptr<GoalHandle> goal_handle);

  void handle_accepted(std::shared_ptr<GoalHandle> goal_handle);

  void execute(std::shared_ptr<GoalHandle> goal_handle);

  // ── Service servers ──────────────────────────────────────────────────
  rclcpp::Service<RegisterTool>::SharedPtr   register_srv_;
  rclcpp::Service<DeregisterTool>::SharedPtr deregister_srv_;
  rclcpp::Service<ListTools>::SharedPtr      list_srv_;

  void handle_register(
    const std::shared_ptr<RegisterTool::Request> req,
    std::shared_ptr<RegisterTool::Response> res);

  void handle_deregister(
    const std::shared_ptr<DeregisterTool::Request> req,
    std::shared_ptr<DeregisterTool::Response> res);

  void handle_list(
    const std::shared_ptr<ListTools::Request> req,
    std::shared_ptr<ListTools::Response> res);

  // ── Tool registry ────────────────────────────────────────────────────
  std::mutex tool_map_mutex_;
  std::unordered_map<std::string, ToolEntry> tool_map_;
};

}  // namespace tool_manager
