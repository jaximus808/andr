#include "andr/brain.h"

RobotBrain::RobotBrain() : Node("robot_brain") {
//    // A. CREATE BLACKBOARD (The Shared Memory)
  //
        auto param_desc = rcl_interfaces::msg::ParameterDescriptor();
        param_desc.floating_point_range.resize(1);
        param_desc.floating_point_range[0].from_value = 0.5; 
        param_desc.floating_point_range[0].to_value = 100;
        param_desc.floating_point_range[0].step = 0.05;
        param_desc.description = "Rate in Hz to run behavior tree at";

        this->declare_parameter("rate_hz", float_t(30), param_desc);
    blackboard_ = BT::Blackboard::create();
    blackboard_->set<bool>("has_task", false); // Default: No task

    // B. SETUP ROS SUBSCRIBER (The Bridge)
    // This updates the blackboard when Python sends a message
    subscription_ = this->create_subscription<std_msgs::msg::String>(
        "/incoming_task", 10,
        [this](const std_msgs::msg::String::SharedPtr msg) {
            RCLCPP_INFO(this->get_logger(), "RECIEVED MSG: %s", msg->data.c_str());
            
            // WRITE TO BLACKBOARD
            this->blackboard_->set("has_task", true);
        }
    );
//    
//    // C. SETUP BEHAVIOR TREE FACTORY
        // start with the timer cancelled
}

void RobotBrain::init() {
    auto node_ptr = shared_from_this();
    BT::BehaviorTreeFactory factory;
    factory.registerNodeType<TaskCheck>("CheckBlackboard");
    factory.registerNodeType<TaskMode>("ExecuteTask");
    factory.registerBuilder<Wander>(
    "IdleWander",
    [node_ptr](const std::string& name, const BT::NodeConfiguration& config) {
      return std::make_unique<Wander>(name, config, node_ptr);
    });

//    // D. CREATE THE TREE
    // Important: We pass the blackboard to the tree here!
    tree_ = factory.createTreeFromFile("./config/behavior_tree.xml", blackboard_);


    std::chrono::milliseconds rate(int32_t(1000.0 / this->get_parameter("rate_hz").as_double()));
    timer_ = this->create_wall_timer(rate, 
                                   std::bind(&RobotBrain::timer_callback, this));

}

void RobotBrain::run() {

}


void RobotBrain::timer_callback() {
    tree_.tickRoot();
    return;
}
