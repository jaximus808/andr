#ifndef ANDR_WANDER_H
#define ANDR_WANDER_H

#include <behaviortree_cpp_v3/action_node.h>

class Wander : public BT::StatefulActionNode {
public:
    Wander(const std::string& name, const BT::NodeConfiguration& config);

    static BT::PortsList providedPorts();

    BT::NodeStatus onStart() override;

    BT::NodeStatus onRunning() override;

    void onHalted() override; 
};


#endif
