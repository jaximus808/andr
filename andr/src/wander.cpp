#include "andr/wander.h"

Wander::Wander(const std::string& name, const BT::NodeConfiguration& config) 
        : BT::StatefulActionNode(name, config) {}

BT::PortsList Wander::providedPorts() {
  return {};
}

BT::NodeStatus Wander::onStart() {
    std::cout << "[Idle] Starting to wander..." << std::endl;
    return BT::NodeStatus::RUNNING;
}

BT::NodeStatus Wander::onRunning() { 
        // Print dots to show it's working
    std::cout << "." << std::flush;
    return BT::NodeStatus::RUNNING;
}

void Wander::onHalted() {
    std::cout << "\n[Idle] INTERRUPTED BY HIGHER PRIORITY!" << std::endl;
    std::cout << "[Idle] Stopping wheels." << std::endl;
}
