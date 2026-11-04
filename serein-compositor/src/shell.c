/*
 * SereinOS Compositor - Shell implementation
 */

#include "shell.h"
#include "log.h"
#include <stdlib.h>
#include <string.h>

struct serein_shell shell;

void serein_shell_init(struct serein_server *server) {
    shell.server = server;
    wl_list_init(&shell.surfaces);
    shell.avatar_surface = NULL;
    
    // Create scene trees for each layer
    shell.avatar_tree = wlr_scene_tree_create(&server->scene->tree);
    shell.notification_tree = wlr_scene_tree_create(&server->scene->tree);
    shell.launcher_tree = wlr_scene_tree_create(&server->scene->tree);
    shell.overlay_tree = wlr_scene_tree_create(&server->scene->tree);
    
    // Set layer ordering (bottom to top)
    // Background -> Bottom -> Top -> Overlay
    // The avatar runs in the OVERLAY layer as the primary surface
    
    serein_log_info("Shell initialized");
}

void serein_shell_fini(struct serein_shell *shell) {
    struct serein_shell_surface *surf, *tmp;
    wl_list_for_each_safe(surf, tmp, &shell->surfaces, link) {
        serein_shell_destroy_surface(surf);
    }
}

struct serein_shell_surface *serein_shell_create_surface(struct serein_shell *shell, serein_surface_type_t type) {
    struct serein_shell_surface *surf = calloc(1, sizeof(*surf));
    if (!surf) return NULL;
    
    surf->shell = shell;
    surf->type = type;
    surf->mapped = false;
    surf->fullscreen = false;
    
    // Create scene tree for this surface
    switch (type) {
        case SEREIN_SURFACE_AVATAR:
            surf->scene_tree = wlr_scene_tree_create(shell->avatar_tree);
            surf->layer = SEREIN_SHELL_LAYER_OVERLAY;
            surf->fullscreen = true;
            shell->avatar_surface = surf;
            break;
        case SEREIN_SURFACE_NOTIFICATION:
            surf->scene_tree = wlr_scene_tree_create(shell->notification_tree);
            surf->layer = SEREIN_SHELL_LAYER_TOP;
            break;
        case SEREIN_SURFACE_LAUNCHER:
        case SEREIN_SURFACE_SETTINGS:
        case SEREIN_SURFACE_DEBUG:
            surf->scene_tree = wlr_scene_tree_create(shell->overlay_tree);
            surf->layer = SEREIN_SHELL_LAYER_OVERLAY;
            break;
        case SEREIN_SURFACE_CURSOR:
            surf->scene_tree = wlr_scene_tree_create(&surf->shell->server->scene->tree);
            surf->layer = SEREIN_SHELL_LAYER_OVERLAY;
            break;
    }
    
    wl_list_insert(&shell->surfaces, &surf->link);
    
    return surf;
}

void serein_shell_destroy_surface(struct serein_shell_surface *surf) {
    if (surf->scene_tree) {
        wlr_scene_node_destroy(&surf->scene_tree->node);
    }
    
    if (surf->surface) {
        wlr_surface_destroy(surf->surface);
    }
    
    wl_list_remove(&surf->link);
    free(surf);
}

void serein_shell_surface_set_fullscreen(struct serein_shell_surface *surf, bool fullscreen) {
    surf->fullscreen = fullscreen;
    if (surf->shell->avatar_surface == surf) {
        // Avatar is always fullscreen
        surf->fullscreen = true;
    }
}

void serein_shell_surface_set_position(struct serein_shell_surface *surf, int x, int y) {
    surf->x = x;
    surf->y = y;
    if (surf->scene_tree) {
        wlr_scene_node_set_position(&surf->scene_tree->node, x, y);
    }
}

void serein_shell_arrange(struct serein_shell *shell) {
    // Arrange surfaces in their respective layers
    // Avatar always centered fullscreen
    if (shell->avatar_surface && shell->avatar_surface->scene_tree) {
        struct wlr_output *output;
        wl_list_for_each(output, &shell->server->output_layout->outputs, link) {
            int cx = output->width / 2;
            int cy = output->height / 2;
            wlr_scene_node_set_position(&shell->avatar_surface->scene_tree->node, 
                                        cx - output->width / 2, 
                                        cy - output->height / 2);
        }
    }
    
    // Arrange notifications from top-right
    // Arrange launcher centered
    // etc.
}