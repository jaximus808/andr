
#ifndef SKILL_H_
#define SKILL_H_
#include <memory>
#include <string>
#include <iostream>

class Skill {
public:
    virtual ~Skill() = default;
    virtual void execute(const std::string& config) = 0; 

    virtual std::string get_name() = 0;
};

#endif
