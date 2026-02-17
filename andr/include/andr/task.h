#ifndef ANDR_TASK_H
#define ANDR_TASK_H

#include <behaviortree_cpp_v3/action_node.h>

class TaskMode: public BT::SyncActionNode {
public:
  TaskMode(const std::string& name, const BT::NodeConfiguration& config);

  static BT::PortsList providedPorts();

  BT::NodeStatus tick() override;

};


#endif 
