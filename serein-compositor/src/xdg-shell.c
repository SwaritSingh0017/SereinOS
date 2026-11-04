#include "compositor.h"
#include <wlr/types/wlr_xdg_shell.h>
#include <wlr/util/log.h>

static void xdg_toplevel_map_notify(struct wl_listener *listener, void *data) {
    struct wlr_xdg_toplevel *toplevel = data;
    wlr_log(WLR_DEBUG, "XDG toplevel mapped: %s", toplevel->title);
}

static void xdg_toplevel_unmap_notify(struct wl_listener *listener, void *data) {
    struct wlr_xdg_toplevel *toplevel = data;
    wlr_log(WLR_DEBUG, "XDG toplevel unmapped");
}

static void xdg_toplevel_destroy_notify(struct wl_listener *listener, void *data) {
    struct wlr_xdg_toplevel *toplevel = data;
    wlr_log(WLR_DEBUG, "XDG toplevel destroyed");
}

static void new_xdg_surface_notify(struct wl_listener *listener, void *data) {
    struct wlr_xdg_surface *surface = data;
    
    if (surface->role == WLR_XDG_SURFACE_ROLE_TOPLEVEL) {
        struct wlr_xdg_toplevel *toplevel = surface->toplevel;
        
        struct wl_listener *map = calloc(1, sizeof(struct wl_listener));
        map->notify = xdg_toplevel_map_notify;
        wl_signal_add(&toplevel->events.map, map);
        
        struct wl_listener *unmap = calloc(1, sizeof(struct wl_listener));
        unmap->notify = xdg_toplevel_unmap_notify;
        wl_signal_add(&toplevel->events.unmap, unmap);
        
        struct wl_listener *destroy = calloc(1, sizeof(struct wl_listener));
        destroy->notify = xdg_toplevel_destroy_notify;
        wl_signal_add(&toplevel->events.destroy, destroy);
    }
}

void xdg_shell_init(struct serein_compositor *comp) {
    comp->xdg_shell = wlr_xdg_shell_create(comp->wl_display, 3);
    
    comp->new_xdg_surface.notify = new_xdg_surface_notify;
    wl_signal_add(&comp->xdg_shell->events.new_surface, &comp->new_xdg_surface);
}