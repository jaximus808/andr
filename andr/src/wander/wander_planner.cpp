#include "andr/wander/wander_planner.h"

#include <yaml-cpp/yaml.h>
#include <ament_index_cpp/get_package_share_directory.hpp>


WanderPlanner::WanderPlanner() {
    this->load_skills();
}

void WanderPlanner::load_skills() {
    std::string yaml_path = ament_index_cpp::get_package_share_directory("andr") + "/skills.yaml";
    YAML::Node config = YAML::LoadFile(yaml_path);
    for (const auto& node : config["skills"]) {
        WanderPlanner::SkillState skill;
        skill.id = node["name"].as<std::string>();
        skill.description = node["description"].as<std::string>();
        skill.default_params = "test";///node["default_params"].as<std::string>();
        //skill.last_execution_time = rclcpp::Time(0); // Never run
        
        available_skills_.push_back(skill);
    }

}

WanderPlanner::SkillState WanderPlanner::decide_skill() {
    // TODO: add cooldown filtering once timers are wired up
    if (available_skills_.empty()) {
        SkillState none_skill;
        none_skill.id = "NONE";
        return none_skill;
    }

    //for now pick the first, make some better algorithm later

    WanderPlanner::SkillState picked_skill = available_skills_[0];
    return picked_skill;

}


