/*
 * SereinOS Compositor - Main entry point
 * Custom Wayland compositor for SereinOS that runs the avatar as the primary shell.
 */

#define _GNU_SOURCE
#include <wayland-server-core.h>
#include <wlroots/wlr_compositor.h>
#include <wlroots/wlr_output.h>
#include <wlroots/wlr_output_layout.h>
#include <wlroots/wlr_renderer.h>
#include <wlroots/wlr_seat.h>
#include <wlroots/wlr_xdg_shell.h>
#include <wlroots/wlr_layer_shell_v1.h>
#include <wlroots/wlr_scene.h>
#include <wlroots/wlr_log.h>
#include <wlroots/wlr_xwayland.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "config.h"
#include "compositor.h"
#include "log.h"

struct serein_server {
    struct wl_display *display;
    struct wl_event_loop *event_loop;
    struct wlr_renderer *renderer;
    struct wlr_allocator *allocator;
    struct wlr_compositor *compositor;
    struct wlr_output_layout *output_layout;
    struct wlr_scene *scene;
    struct wlr_xdg_shell *xdg_shell;
    struct wlr_layer_shell_v1 *layer_shell;
    struct wlr_seat *seat;
    struct wlr_xwayland *xwayland;
    
    struct serein_config config;
    bool running;
};

static struct serein_server server;

static void handle_new_output(struct wl_listener *listener, void *data) {
    struct wlr_output *output = data;
    serein_log_info("New output: %s", output->name);
    
    wlr_output_init_render(output, server.allocator, server.renderer);
    
    struct wlr_output_state state;
    wlr_output_state_init(&state);
    wlr_output_state_set_enabled(&state, true);
    
    struct wlr_output_mode *mode = wlr_output_preferred_mode(output);
    if (mode) {
        wlr_output_state_set_mode(&state, mode);
    }
    
    wlr_output_commit_state(output, &state);
    wlr_output_state_finish(&state);
    
    wlr_output_layout_add_auto(server.output_layout, output);
}

static void handle_new_xdg_surface(struct wl_listener *listener, void *data) {
    struct wlr_xdg_surface *xdg_surface = data;
    serein_log_info("New XDG surface: %p", (void*)xdg_surface);
    
    if (xdg_surface->role == WLR_XDG_SURFACE_ROLE_TOPLEVEL) {
        // Handle toplevel window - for SereinOS, we might want to
        // manage these specially or delegate to the avatar
    }
}

static void handle_new_layer_surface(struct wl_listener *listener, void *data) {
    struct wlr_layer_surface_v1 *layer_surface = data;
    serein_log_info("New layer surface: %s, layer: %d", 
                    layer_surface->output ? layer_surface->output->name : "none",
                    layer_surface->current.layer);
}

static void handle_seat_request_cursor(struct wl_listener *listener, void *data) {
    struct wlr_seat_pointer_request_set_cursor_event *event = data;
    wlr_seat_pointer_set_cursor(server.seat, event->seat_client, 
                                 event->surface, event->hotspot_x, event->hotspot_y);
}

static void handle_seat_request_set_selection(struct wl_listener *listener, void *data) {
    struct wlr_seat_request_set_selection_event *event = data;
    wlr_seat_set_selection(server.seat, event->source, event->serial);
}

static void signal_handler(int signal) {
    serein_log_info("Received signal %d, shutting down...", signal);
    server.running = false;
    wl_display_terminate(server.display);
}

int main(int argc, char *argv[]) {
    // Initialize logging
    serein_log_init();
    
    // Parse config
    if (!serein_config_parse(argc, argv, &server.config)) {
        serein_log_error("Failed to parse config");
        return 1;
    }
    
    // Setup signal handlers
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    // Create Wayland display
    server.display = wl_display_create();
    if (!server.display) {
        serein_log_error("Failed to create Wayland display");
        return 1;
    }
    
    server.event_loop = wl_display_get_event_loop(server.display);
    
    // Create wlroots backend
    struct wlr_backend *backend = wlr_backend_autocreate(server.event_loop, NULL);
    if (!backend) {
        serein_log_error("Failed to create wlroots backend");
        return 1;
    }
    
    // Create renderer
    server.renderer = wlr_renderer_autocreate(backend);
    if (!server.renderer) {
        serein_log_error("Failed to create renderer");
        return 1;
    }
    
    wlr_renderer_init_wl_display(server.renderer, server.display);
    
    // Create allocator
    server.allocator = wlr_allocator_autocreate(backend, server.renderer);
    if (!server.allocator) {
        serein_log_error("Failed to create allocator");
        return 1;
    }
    
    // Create scene graph
    server.scene = wlr_scene_create();
    if (!server.scene) {
        serein_log_error("Failed to create scene");
        return 1;
    }
    
    // Create output layout
    server.output_layout = wlr_output_layout_create(server.display);
    
    // Create compositor
    server.compositor = wlr_compositor_create(server.display, 5, server.renderer);
    
    // Create XDG shell
    server.xdg_shell = wlr_xdg_shell_create(server.display, 3);
    
    // Create layer shell
    server.layer_shell = wlr_layer_shell_v1_create(server.display, 4);
    
    // Create seat
    server.seat = wlr_seat_create(server.display, "seat0");
    
    // Listen for events
    static struct wl_listener new_output = { .notify = handle_new_output };
    wl_signal_add(&backend->events.new_output, &new_output);
    
    static struct wl_listener new_xdg_surface = { .notify = handle_new_xdg_surface };
    wl_signal_add(&server.xdg_shell->events.new_surface, &new_xdg_surface);
    
    static struct wl_listener new_layer_surface = { .notify = handle_new_layer_surface };
    wl_signal_add(&server.layer_shell->events.new_surface, &new_layer_surface);
    
    static struct wl_listener request_cursor = { .notify = handle_seat_request_cursor };
    wl_signal_add(&server.seat->events.request_set_cursor, &request_cursor);
    
    static struct wl_listener request_selection = { .notify = handle_seat_request_set_selection };
    wl_signal_add(&server.seat->events.request_set_selection, &request_selection);
    
    // Start backend
    if (!wlr_backend_start(backend)) {
        serein_log_error("Failed to start backend");
        return 1;
    }
    
    // Create XWayland
    if (server.config.enable_xwayland) {
        server.xwayland = wlr_xwayland_create(server.display, server.compositor, true);
        if (!server.xwayland) {
            serein_log_warn("Failed to create XWayland");
        }
    }
    
    // Set up socket
    const char *socket = wl_display_add_socket_auto(server.display);
    if (!socket) {
        serein_log_error("Failed to add socket");
        return 1;
    }
    
    setenv("WAYLAND_DISPLAY", socket, true);
    serein_log_info("Running on Wayland socket: %s", socket);
    serein_log_info("SereinOS Compositor started");
    
    // Run event loop
    server.running = true;
    wl_display_run(server.display);
    
    // Cleanup
    serein_log_info("Shutting down...");
    wlr_backend_destroy(backend);
    wl_display_destroy(server.display);
    
    return 0;
}