#include "andr/taskcheck.h"

TaskCheck::TaskCheck(const std::string& name, const BT::NodeConfiguration& config)
  : BT::ConditionNode(name, config) {}

BT::PortsList TaskCheck::providedPorts() {
  return {BT::InputPort<std::string>("variable")};
}

BT::NodeStatus TaskCheck::tick() {
    std::string var_name;
    getInput("variable", var_name);
        
        // READ THE BLACKBOARD
    bool is_true = false;
    if (config().blackboard->get(var_name, is_true) && is_true) {
        return BT::NodeStatus::SUCCESS; 
    }
    return BT::NodeStatus::FAILURE;
}
