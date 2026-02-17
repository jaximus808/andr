#ifndef GO_NEAREST_H
#define GO_NEAREST_H

#include "andr/skills/skill.h"

class GoToNearest : public Skill {

public:
    void execute(const std::string& config) override; 

    std::string get_name() override; 
};

#endif
