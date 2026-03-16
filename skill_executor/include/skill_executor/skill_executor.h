#pragma once

#include <memory>
#include <string>
#include <thread>
#include <unordered_map>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include <andr/action/execute_skill.hpp>

namespace skill_executor
{

struct SkillEntry {
  std::string action_server;
  std::string description;
  rclcpp_action::Client<andr::action::ExecuteSkill>::SharedPtr client;
};

class SkillExecutorNode : public rclcpp::Node
{
public:
  using ExecuteSkill = andr::action::ExecuteSkill;
  using GoalHandle   = rclcpp_action::ServerGoalHandle<ExecuteSkill>;

  explicit SkillExecutorNode(const rclcpp::NodeOptions & options = rclcpp::NodeOptions{});

private:
  rclcpp_action::Server<ExecuteSkill>::SharedPtr action_server_;

  /// skill_name -> SkillEntry (action client + metadata)
  std::unordered_map<std::string, SkillEntry> skill_map_;

  // ── Config loading ──────────────────────────────────────────────────────
  void load_config(const std::string & yaml_path);

  // ── Action server callbacks ─────────────────────────────────────────────
  rclcpp_action::GoalResponse handle_goal(
    const rclcpp_action::GoalUUID & uuid,
    std::shared_ptr<const ExecuteSkill::Goal> goal);

  rclcpp_action::CancelResponse handle_cancel(
    std::shared_ptr<GoalHandle> goal_handle);

  void handle_accepted(std::shared_ptr<GoalHandle> goal_handle);

  // ── Execution (runs in a detached thread) ──────────────────────────────
  void execute(std::shared_ptr<GoalHandle> goal_handle);
};

}  // namespace skill_executor
