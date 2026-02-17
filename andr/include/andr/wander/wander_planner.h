#ifndef SKILL_MANAGER_H
#define SKILL_MANAGER_H

#include<vector>
#include<string>

class WanderPlanner {
public:

struct SkillState {
    std::string id;
    std::string description; 
    std::string default_params;
   // double cooldown_seconds = 5.0; // <-- You must add this!
   // rclcpp::Time last_execution_time; 
   // int execution_count = 0; 
   // bool is_available(const rclcpp::Time& current_time) const {
   //     if (execution_count == 0) return true;
   //     auto elapsed = (current_time - last_execution_time).seconds();
   //     return elapsed >= cooldown_seconds;
   // }
};
    WanderPlanner();

    WanderPlanner::SkillState decide_skill();

    void on_skill_completed(const std::string& skill_id, bool success);

private:

    void load_skills();
    std::vector<SkillState> available_skills_;
    //void create_skill_queue(); 

    //std::queue<std::make_unqiue<SkillState>> skill_queue;
};


#endif // !ABILITY_MANAGER_H


