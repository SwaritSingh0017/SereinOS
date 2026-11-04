/*
 * SereinOS Compositor - Configuration
 */

#ifndef SEREIN_CONFIG_H
#define SEREIN_CONFIG_H

#include <stdbool.h>
#include <stdint.h>

struct serein_output_config {
    char name[64];
    int width;
    int height;
    int refresh_rate;
    bool enabled;
    int x, y;
    float scale;
    int transform;
};

struct serein_input_config {
    char identifier[128];
    char mapping[64];
    bool tap_to_click;
    bool natural_scroll;
    bool left_handed;
    double accel_speed;
    char accel_profile[32];
};

struct serein_config {
    bool enable_xwayland;
    bool enable_debug;
    char socket_name[64];
    char cursor_theme[64];
    int cursor_size;
    
    struct serein_output_config outputs[16];
    int output_count;
    
    struct serein_input_config inputs[32];
    int input_count;
    
    char xcursor_theme[64];
    int xcursor_size;
};

bool serein_config_parse(int argc, char *argv[], struct serein_config *config);

#endif