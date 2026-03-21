#pragma once

#include <memory>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include <andr/action/execute_skill.hpp>
#include <andr/srv/get_available_tools.hpp>

namespace skill_executor
{

struct SkillParameter {
  std::string name;
  std::string type;        // "string", "float", "int", "bool", "array"
  bool required = true;
  std::string description;
};

struct SkillDefinition {
  std::string name;
  std::string description;
  std::vector<SkillParameter> parameters;
  std::string returns   = "void";
  std::string category  = "general";
  std::vector<std::string> tags;
};

struct SkillEntry {
  std::string action_server;
  std::string description;
  rclcpp_action::Client<andr::action::ExecuteSkill>::SharedPtr client;
};

class SkillExecutorNode : public rclcpp::Node
{
public:
  using ExecuteSkill     = andr::action::ExecuteSkill;
  using GoalHandle       = rclcpp_action::ServerGoalHandle<ExecuteSkill>;
  using GetAvailableTools = andr::srv::GetAvailableTools;

  explicit SkillExecutorNode(const rclcpp::NodeOptions & options = rclcpp::NodeOptions{});

private:
  rclcpp_action::Server<ExecuteSkill>::SharedPtr action_server_;
  rclcpp::Service<GetAvailableTools>::SharedPtr tools_service_;

  /// skill_name -> SkillEntry (action client + metadata)
  std::unordered_map<std::string, SkillEntry> skill_map_;

  /// Full skill definitions loaded from skills.yaml
  std::unordered_map<std::string, SkillDefinition> skill_defs_;

  // ── Config loading ──────────────────────────────────────────────────────
  void load_config(const std::string & yaml_path);
  void load_skills_yaml(const std::string & yaml_path);

  // ── Service callback ──────────────────────────────────────────────────
  void handle_get_available_tools(
    const std::shared_ptr<GetAvailableTools::Request> request,
    std::shared_ptr<GetAvailableTools::Response> response);

  // ── JSON helpers ──────────────────────────────────────────────────────
  std::string build_tools_json() const;
  static std::string escape_json(const std::string & s);

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
