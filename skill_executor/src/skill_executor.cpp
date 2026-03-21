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
  // Declare parameters for config paths
  this->declare_parameter<std::string>("config_yaml", "");
  this->declare_parameter<std::string>("skills_yaml", "");

  std::string config_path =
    this->get_parameter("config_yaml").as_string();
  std::string skills_yaml_path =
    this->get_parameter("skills_yaml").as_string();

  // Load full skill definitions from skills.yaml (metadata for the agent)
  if (!skills_yaml_path.empty()) {
    load_skills_yaml(skills_yaml_path);
  } else {
    RCLCPP_WARN(get_logger(), "No skills_yaml parameter set — tool metadata will be empty.");
  }

  // Load routing config (which skills this executor can dispatch)
  if (!config_path.empty()) {
    load_config(config_path);
  } else {
    RCLCPP_WARN(get_logger(), "No config_yaml parameter set — no skills registered.");
  }

  // Action server for skill dispatch
  action_server_ = rclcpp_action::create_server<ExecuteSkill>(
    this,
    "skill_executor",
    std::bind(&SkillExecutorNode::handle_goal,     this, _1, _2),
    std::bind(&SkillExecutorNode::handle_cancel,   this, _1),
    std::bind(&SkillExecutorNode::handle_accepted, this, _1)
  );

  // Service: return available tools to the agent
  tools_service_ = this->create_service<GetAvailableTools>(
    "get_available_tools",
    std::bind(&SkillExecutorNode::handle_get_available_tools, this, _1, _2)
  );

  RCLCPP_INFO(get_logger(),
    "SkillExecutorNode ready — %zu skill(s) registered, %zu definition(s) loaded.",
    skill_map_.size(), skill_defs_.size());
}

// ── Config loading (routing table) ──────────────────────────────────────────

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

// ── Skills YAML loading (full metadata) ─────────────────────────────────────

void SkillExecutorNode::load_skills_yaml(const std::string & yaml_path)
{
  RCLCPP_INFO(get_logger(), "Loading skill definitions from '%s'", yaml_path.c_str());

  YAML::Node config;
  try {
    config = YAML::LoadFile(yaml_path);
  } catch (const std::exception & e) {
    RCLCPP_ERROR(get_logger(), "Failed to load skills YAML '%s': %s", yaml_path.c_str(), e.what());
    return;
  }

  if (!config["skills"] || !config["skills"].IsSequence()) {
    RCLCPP_WARN(get_logger(), "Skills YAML has no 'skills' sequence.");
    return;
  }

  for (const auto & skill_node : config["skills"]) {
    SkillDefinition def;
    def.name        = skill_node["name"].as<std::string>("");
    def.description = skill_node["description"].as<std::string>("");
    def.returns     = skill_node["returns"].as<std::string>("void");
    def.category    = skill_node["category"].as<std::string>("general");

    if (skill_node["parameters"] && skill_node["parameters"].IsSequence()) {
      for (const auto & param_node : skill_node["parameters"]) {
        SkillParameter param;
        param.name        = param_node["name"].as<std::string>("");
        param.type        = param_node["type"].as<std::string>("string");
        param.required    = param_node["required"].as<bool>(true);
        param.description = param_node["description"].as<std::string>("");
        def.parameters.push_back(param);
      }
    }

    if (skill_node["tags"] && skill_node["tags"].IsSequence()) {
      for (const auto & tag : skill_node["tags"]) {
        def.tags.push_back(tag.as<std::string>());
      }
    }

    if (!def.name.empty()) {
      RCLCPP_INFO(get_logger(), "  Loaded definition for skill '%s' (%zu params)",
        def.name.c_str(), def.parameters.size());
      skill_defs_[def.name] = std::move(def);
    }
  }

  RCLCPP_INFO(get_logger(), "Loaded %zu skill definition(s).", skill_defs_.size());
}

// ── Service: get_available_tools ────────────────────────────────────────────

void SkillExecutorNode::handle_get_available_tools(
  const std::shared_ptr<GetAvailableTools::Request> /*request*/,
  std::shared_ptr<GetAvailableTools::Response> response)
{
  RCLCPP_INFO(get_logger(), "get_available_tools service called.");
  response->success    = true;
  response->message    = "OK";
  response->tools_json = build_tools_json();
}

// ── JSON helpers ────────────────────────────────────────────────────────────

std::string SkillExecutorNode::escape_json(const std::string & s)
{
  std::string result;
  result.reserve(s.size() + 16);
  for (char c : s) {
    switch (c) {
      case '"':  result += "\\\""; break;
      case '\\': result += "\\\\"; break;
      case '\n': result += "\\n"; break;
      case '\r': result += "\\r"; break;
      case '\t': result += "\\t"; break;
      default:   result += c;
    }
  }
  return result;
}

std::string SkillExecutorNode::build_tools_json() const
{
  // Return only skills that are both defined (skills.yaml) AND routable (config)
  std::ostringstream ss;
  ss << "[";
  bool first = true;

  for (const auto & [name, entry] : skill_map_) {
    auto def_it = skill_defs_.find(name);
    if (def_it == skill_defs_.end()) {
      // Skill is routable but has no full definition — build a minimal one
      // from the routing config
      if (!first) ss << ",";
      first = false;
      ss << "{\"name\":\"" << escape_json(name) << "\","
         << "\"description\":\"" << escape_json(entry.description) << "\","
         << "\"parameters\":[],"
         << "\"returns\":\"void\","
         << "\"category\":\"general\","
         << "\"tags\":[]}";
      continue;
    }

    const auto & def = def_it->second;
    if (!first) ss << ",";
    first = false;

    ss << "{\"name\":\"" << escape_json(def.name) << "\","
       << "\"description\":\"" << escape_json(def.description) << "\","
       << "\"parameters\":[";

    for (size_t i = 0; i < def.parameters.size(); ++i) {
      const auto & p = def.parameters[i];
      if (i > 0) ss << ",";
      ss << "{\"name\":\"" << escape_json(p.name) << "\","
         << "\"type\":\"" << escape_json(p.type) << "\","
         << "\"required\":" << (p.required ? "true" : "false") << ","
         << "\"description\":\"" << escape_json(p.description) << "\"}";
    }

    ss << "],\"returns\":\"" << escape_json(def.returns) << "\","
       << "\"category\":\"" << escape_json(def.category) << "\","
       << "\"tags\":[";

    for (size_t i = 0; i < def.tags.size(); ++i) {
      if (i > 0) ss << ",";
      ss << "\"" << escape_json(def.tags[i]) << "\"";
    }

    ss << "]}";
  }

  ss << "]";
  return ss.str();
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

  // Wait for goal acceptance (the multi-threaded executor handles callbacks)
  if (goal_future.wait_for(10s) != std::future_status::ready)
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

  // Wait for the result (the multi-threaded executor handles callbacks)
  auto result_future = entry.client->async_get_result(downstream_handle);
  if (result_future.wait_for(60s) != std::future_status::ready)
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
  auto node = std::make_shared<skill_executor::SkillExecutorNode>();
  rclcpp::executors::MultiThreadedExecutor executor;
  executor.add_node(node);
  executor.spin();
  rclcpp::shutdown();
  return 0;
}
