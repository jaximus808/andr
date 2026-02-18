#include <rclcpp/rclcpp.hpp>
#include "andr/brain.h"
#include "andr/wander/wander_action_server.h"

int main(int argc, char **argv) {
    rclcpp::init(argc, argv);
    auto node_brain = std::make_shared<RobotBrain>();
    
    node_brain->init(); 

    auto node_wander = std::make_shared<WanderActionServer>();

    rclcpp::executors::MultiThreadedExecutor executor;
    
    executor.add_node(node_brain);
    executor.add_node(node_wander);


    executor.spin();

    rclcpp::shutdown();
    return 0;
}
