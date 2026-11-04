#pragma once

#include <wayland-server-core.h>
#include <wlr/backend.h>
#include <wlr/render/wlr_renderer.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_output.h>
#include <wlr/types/wlr_output_layout.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/types/wlr_xdg_shell.h>
#include <wlr/types/wlr_layer_shell_v1.h>
#include <wlr/types/wlr_foreign_toplevel_management_v1.h>
#include <wlr/types/wlr_screencopy_v1.h>
#include <wlr/types/wlr_fractional_scale_v1.h>
#include <wlr/types/wlr_viewporter.h>
#include <wlr/types/wlr_cursor_shape_v1.h>
#include <wlr/scene/wlr_scene.h>
#include <wlr/allocator.h>

#define SEREIN_CONFIG_MAX_OUTPUTS 16
#define SEREIN_CONFIG_MAX_KEYBINDS 64

struct serein_output_config {
    char name[64];
    int x, y;
    int width, height;
    float scale;
    int transform;
    bool enabled;
};

struct serein_input_config {
    char identifier[128];
    char keymap_layout[8];
    char keymap_variant[32];
    char keymap_options[64];
    int repeat_rate;
    int repeat_delay;
    float accel_speed;
    bool natural_scroll;
};

struct serein_keybind {
    char key[32];
    uint32_t modifiers;
    char action[64];
    char command[256];
};

struct serein_config {
    struct serein_output_config outputs[SEREIN_CONFIG_MAX_OUTPUTS];
    int output_count;
    
    struct serein_input_config input;
    
    struct serein_keybind keybinds[SEREIN_CONFIG_MAX_KEYBINDS];
    int keybind_count;
    
    char cursor_theme[64];
    int cursor_size;
    bool cursor_hide_timeout;
    int cursor_hide_seconds;
    
    char avatar_host[64];
    int avatar_port;
    bool avatar_enabled;
    
    bool animations_enabled;
    float animation_duration;
    int gaps_inner;
    int gaps_outer;
};

struct serein_compositor {
    struct wl_display *wl_display;
    struct wlr_backend *backend;
    struct wlr_renderer *renderer;
    struct wlr_allocator *allocator;
    struct wlr_scene *scene;
    struct wlr_output_layout *output_layout;
    struct wlr_compositor *compositor;
    struct wlr_seat *seat;
    struct wlr_xdg_shell *xdg_shell;
    struct wlr_layer_shell_v1 *layer_shell;
    struct wlr_foreign_toplevel_management_v1 *foreign_toplevel;
    struct wlr_screencopy_manager_v1 *screencopy;
    struct wlr_fractional_scale_manager_v1 *fractional_scale;
    struct wlr_viewporter *viewporter;
    struct wlr_cursor_shape_manager_v1 *cursor_shape;
    
    struct serein_config config;
    
    struct wl_list outputs; // serein_output::link
    struct wl_list keyboards; // serein_keyboard::link
    struct wl_list pointers; // serein_pointer::link
    struct wl_list touches; // serein_touch::link
    
    struct serein_scene *scene_root;
    struct serein_damage_ring *damage_ring;
    struct serein_avatar_bridge *avatar_bridge;
    
    // Signal listeners
    struct wl_listener new_output;
    struct wl_listener new_input;
    struct wl_listener request_cursor;
    struct wl_listener new_xdg_surface;
    struct wl_listener new_layer_surface;
    struct wl_listener new_foreign_toplevel;
    struct wl_listener new_fractional_scale;
    struct wl_listener new_viewport;
    struct wl_listener set_cursor_shape;
    struct wl_listener copy_output;
    struct wl_listener copy_region;
};

extern struct serein_compositor compositor;

void load_config(const char *path);