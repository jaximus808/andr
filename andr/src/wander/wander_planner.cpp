#include "andr/wander/wander_planner.h"

#include <yaml-cpp/yaml.h>
#include <ament_index_cpp/get_package_share_directory.hpp>


WanderPlanner::WanderPlanner() {
    this->load_skills();
}

void WanderPlanner::load_skills() {
    std::string yaml_path = "./config/skills.yaml";
    YAML::Node config = YAML::LoadFile(yaml_path);
    for (const auto& node : config["skills"]) {
        WanderPlanner::SkillState skill;
        skill.id = node["id"].as<std::string>();
        skill.description = node["description"].as<std::string>();
        skill.default_params = "test";///node["default_params"].as<std::string>();
        //skill.last_execution_time = rclcpp::Time(0); // Never run
        
        available_skills_.push_back(skill);
    }

}

WanderPlanner::SkillState WanderPlanner::decide_skill() {
    std::vector<SkillState*> candidates;

    //rclcpp::Time now = this->now();
    // Step 1: Filter out "Cooldown" skills
   // for (auto& skill : available_skills_) {
   //     if (skill.is_available(now)) {
   //         candidates.push_back(&skill);
   //     }
   // }

    if (candidates.empty()) {
        SkillState none_skill; 
        none_skill.id = "NONE";
        return none_skill;
    } 

    //for now pick the first, make some better algorithm later 
    
    WanderPlanner::SkillState picked_skill = *(candidates[0]);
    return picked_skill;

}


