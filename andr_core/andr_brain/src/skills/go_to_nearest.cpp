#include "andr_brain/skills/go_to_nearest.h"
void GoToNearest::execute(const std::string &config) {
        (void)config;
        std::cout << "Going to nearest POI";
}

std::string GoToNearest::get_name() {
    return "GoToNearest";
}

