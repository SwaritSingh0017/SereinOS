#include "compositor.h"
#include <wlr/types/wlr_layer_shell_v1.h>
#include <wlr/util/log.h>

static void layer_surface_map_notify(struct wl_listener *listener, void *data) {
    struct wlr_layer_surface_v1 *layer_surface = data;
    wlr_log(WLR_DEBUG, "Layer surface mapped: %s", layer_surface->namespace);
}

static void layer_surface_unmap_notify(struct wl_listener *listener, void *data) {
    struct wlr_layer_surface_v1 *layer_surface = data;
    wlr_log(WLR_DEBUG, "Layer surface unmapped");
}

static void layer_surface_destroy_notify(struct wl_listener *listener, void *data) {
    struct wlr_layer_surface_v1 *layer_surface = data;
    wlr_log(WLR_DEBUG, "Layer surface destroyed");
}

static void new_layer_surface_notify(struct wl_listener *listener, void *data) {
    struct wlr_layer_surface_v1 *layer_surface = data;
    
    struct wl_listener *map = calloc(1, sizeof(struct wl_listener));
    map->notify = layer_surface_map_notify;
    wl_signal_add(&layer_surface->events.map, map);
    
    struct wl_listener *unmap = calloc(1, sizeof(struct wl_listener));
    unmap->notify = layer_surface_unmap_notify;
    wl_signal_add(&layer_surface->events.unmap, unmap);
    
    struct wl_listener *destroy = calloc(1, sizeof(struct wl_listener));
    destroy->notify = layer_surface_destroy_notify;
    wl_signal_add(&layer_surface->events.destroy, destroy);
    
    // Auto-arrange layer surfaces
    wlr_layer_surface_v1_configure(layer_surface, 0, 0, 1920, 1080);
}

void layer_shell_init(struct serein_compositor *comp) {
    comp->layer_shell = wlr_layer_shell_v1_create(comp->wl_display);
    
    comp->new_layer_surface.notify = new_layer_surface_notify;
    wl_signal_add(&comp->layer_shell->events.new_surface, &comp->new_layer_surface);
}