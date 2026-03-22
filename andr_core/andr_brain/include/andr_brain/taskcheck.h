#ifndef ANDR_TASKCHECK_H
#define ANDR_TASKCHECK_H
#include <behaviortree_cpp_v3/behavior_tree.h>
#include <behaviortree_cpp_v3/action_node.h>

class TaskCheck : public BT::ConditionNode {
public:
    TaskCheck(const std::string& name, const BT::NodeConfiguration& config);

    static BT::PortsList providedPorts();

    BT::NodeStatus tick() override;
};

#endif // !d
