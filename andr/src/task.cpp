#include "andr/task.h"

TaskMode::TaskMode(const std::string& name, const BT::NodeConfiguration& config)
      : BT::SyncActionNode(name, config){}

BT::PortsList TaskMode::providedPorts() {return {};}

BT::NodeStatus TaskMode::tick() {
    std::cout << "\n[ROBOT] !!! EXECUTING WHATSAPP TASK !!!" << std::endl;
    std::cout << "[ROBOT] Fetching item..." << std::endl;
        
        // After finishing, we must clear the blackboard so we can go back to Idle
    config().blackboard->set("has_task", false);
        
    return BT::NodeStatus::SUCCESS;
}


