#include <rclcpp/rclcpp.hpp>
#include "andr/brain.h"
#include "andr/wander/wander_action_server.h"

int main(int argc, char **argv) {
    rclcpp::init(argc, argv);
    auto node_brain = std::make_shared<RobotBrain>();

    rclcpp::executors::MultiThreadedExecutor executor;
    executor.add_node(node_brain);

    bool wander_enabled = node_brain->get_parameter("enable_wander").as_bool();
    if (wander_enabled) {
        node_brain->init();
        auto node_wander = std::make_shared<WanderActionServer>();
        executor.add_node(node_wander);
    } else {
        RCLCPP_INFO(node_brain->get_logger(), "Wander disabled — skipping BT and action server");
    }

    executor.spin();

    rclcpp::shutdown();
    return 0;
}
