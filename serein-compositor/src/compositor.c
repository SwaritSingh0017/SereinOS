#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <wayland-server-core.h>
#include <wlr/backend.h>
#include <wlr/render/wlr_renderer.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_data_device.h>
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
#include <wlr/util/log.h>

#include "compositor.h"
#include "output.h"
#include "input.h"
#include "layer-shell.h"
#include "xdg-shell.h"
#include "foreign-toplevel.h"
#include "screencopy.h"
#include "render.h"
#include "damage.h"
#include "scene.h"
#include "avatar-bridge.h"
#include "config.h"

struct serein_compositor compositor = {0};

static void handle_signal(int signo) {
    wl_display_terminate(compositor.wl_display);
}

static void load_config(const char *path) {
    config_load(path, &compositor.config);
}

int main(int argc, char *argv[]) {
    wlr_log_init(WLR_DEBUG, NULL);
    
    const char *config_path = "/etc/serein/compositor/config.yaml";
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--config") == 0) {
            if (i + 1 < argc) {
                config_path = argv[++i];
            }
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [-c CONFIG]\n", argv[0]);
            return 0;
        }
    }

    load_config(config_path);
    
    compositor.wl_display = wl_display_create();
    if (!compositor.wl_display) {
        wlr_log(WLR_ERROR, "Failed to create Wayland display");
        return 1;
    }

    const char *socket = wl_display_add_socket_auto(compositor.wl_display);
    if (!socket) {
        wlr_log(WLR_ERROR, "Failed to add Wayland socket");
        return 1;
    }
    setenv("WAYLAND_DISPLAY", socket, true);
    wlr_log(WLR_INFO, "Running on Wayland socket '%s'", socket);

    compositor.backend = wlr_backend_autocreate(compositor.wl_display, NULL);
    if (!compositor.backend) {
        wlr_log(WLR_ERROR, "Failed to create backend");
        return 1;
    }

    compositor.renderer = wlr_backend_get_renderer(compositor.backend);
    if (!compositor.renderer) {
        wlr_log(WLR_ERROR, "Failed to get renderer");
        return 1;
    }
    wlr_renderer_init_wl_display(compositor.renderer, compositor.wl_display);

    compositor.allocator = wlr_allocator_autocreate(compositor.backend, compositor.renderer);
    if (!compositor.allocator) {
        wlr_log(WLR_ERROR, "Failed to create allocator");
        return 1;
    }

    compositor.scene = scene_create(compositor.renderer, compositor.allocator);
    if (!compositor.scene) {
        wlr_log(WLR_ERROR, "Failed to create scene");
        return 1;
    }

    compositor.output_layout = wlr_output_layout_create(compositor.wl_display);
    if (!compositor.output_layout) {
        wlr_log(WLR_ERROR, "Failed to create output layout");
        return 1;
    }

    compositor.compositor = wlr_compositor_create(compositor.wl_display, 5, compositor.renderer);
    if (!compositor.compositor) {
        wlr_log(WLR_ERROR, "Failed to create compositor");
        return 1;
    }

    wlr_data_device_manager_create(compositor.wl_display);

    output_init(&compositor);
    input_init(&compositor);
    xdg_shell_init(&compositor);
    layer_shell_init(&compositor);
    foreign_toplevel_init(&compositor);
    screencopy_init(&compositor);
    fractional_scale_init(&compositor);
    viewporter_init(&compositor);
    cursor_shape_init(&compositor);
    avatar_bridge_init(&compositor);
    damage_init(&compositor);
    render_init(&compositor);

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    if (!wlr_backend_start(compositor.backend)) {
        wlr_log(WLR_ERROR, "Failed to start backend");
        return 1;
    }

    wl_display_run(compositor.wl_display);

    wlr_backend_destroy(compositor.backend);
    wl_display_destroy(compositor.wl_display);
    return 0;
}