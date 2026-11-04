/*
 * SereinOS Compositor - Shell integration for Serein avatar
 */

#ifndef SEREIN_SHELL_H
#define SEREIN_SHELL_H

#include <wayland-server-core.h>
#include <wlroots/wlr_scene.h>
#include <wlroots/wlr_xdg_shell.h>
#include <wlroots/wlr_layer_shell_v1.h>

struct serein_server;
struct serein_shell_surface;

typedef enum {
    SEREIN_SHELL_LAYER_BACKGROUND = 0,
    SEREIN_SHELL_LAYER_BOTTOM = 1,
    SEREIN_SHELL_LAYER_TOP = 2,
    SEREIN_SHELL_LAYER_OVERLAY = 3,
} serein_shell_layer_t;

// Shell surface types for SereinOS
typedef enum {
    SEREIN_SURFACE_AVATAR = 0,      // Main avatar - fullscreen, overlay layer
    SEREIN_SURFACE_NOTIFICATION = 1, // Notifications - top layer
    SEREIN_SURFACE_LAUNCHER = 2,    // App launcher - overlay layer
    SEREIN_SURFACE_SETTINGS = 3,    // Settings panel - overlay layer
    SEREIN_SURFACE_DEBUG = 4,       // Debug overlay - debug layer
    SEREIN_SURFACE_CURSOR = 5,      // Custom cursor
} serein_surface_type_t;

struct serein_shell {
    struct serein_server *server;
    struct wlr_scene_tree *avatar_tree;
    struct wlr_scene_tree *notification_tree;
    struct wlr_scene_tree *launcher_tree;
    struct wlr_scene_tree *overlay_tree;
    
    struct serein_shell_surface *avatar_surface;
    struct wl_list surfaces; // struct serein_shell_surface::link
};

struct serein_shell_surface {
    struct serein_shell *shell;
    struct wlr_scene_tree *scene_tree;
    struct wlr_surface *surface;
    serein_surface_type_t type;
    serein_shell_layer_t layer;
    struct wl_list link;
    bool mapped;
    bool fullscreen;
    int width, height;
    int x, y;
};

void serein_shell_init(struct serein_server *server);
void serein_shell_fini(struct serein_shell *shell);
struct serein_shell_surface *serein_shell_create_surface(struct serein_shell *shell, serein_surface_type_t type);
void serein_shell_destroy_surface(struct serein_shell_surface *shell_surface);
void serein_shell_surface_set_fullscreen(struct serein_shell_surface *shell_surface, bool fullscreen);
void serein_shell_surface_set_position(struct serein_shell_surface *shell_surface, int x, int y);
void serein_shell_arrange(struct serein_shell *shell);

#endif