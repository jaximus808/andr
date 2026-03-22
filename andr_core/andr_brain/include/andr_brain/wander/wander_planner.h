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
};
    WanderPlanner();

    WanderPlanner::SkillState decide_skill();

    void on_skill_completed(const std::string& skill_id, bool success);

private:

    void load_skills();
    std::vector<SkillState> available_skills_;
};


#endif // !ABILITY_MANAGER_H


