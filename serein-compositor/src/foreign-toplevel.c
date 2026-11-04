#include "compositor.h"
#include <wlr/types/wlr_foreign_toplevel_management_v1.h>
#include <wlr/util/log.h>

static void foreign_toplevel_handle_request_activate(struct wl_listener *listener, void *data) {
    struct wlr_foreign_toplevel_handle_v1_activated_event *event = data;
    wlr_log(WLR_DEBUG, "Foreign toplevel activation requested");
    wlr_foreign_toplevel_handle_v1_set_activated(event->toplevel, true);
}

static void foreign_toplevel_handle_request_close(struct wl_listener *listener, void *data) {
    struct wlr_foreign_toplevel_handle_v1 *toplevel = data;
    wlr_log(WLR_DEBUG, "Foreign toplevel close requested");
    wlr_foreign_toplevel_handle_v1_close(toplevel);
}

static void foreign_toplevel_new_handle(struct wl_listener *listener, void *data) {
    struct wlr_foreign_toplevel_handle_v1 *toplevel = data;
    
    struct wl_listener *activate = calloc(1, sizeof(struct wl_listener));
    activate->notify = foreign_toplevel_handle_request_activate;
    wl_signal_add(&toplevel->events.request_activate, activate);
    
    struct wl_listener *close = calloc(1, sizeof(struct wl_listener));
    close->notify = foreign_toplevel_handle_request_close;
    wl_signal_add(&toplevel->events.request_close, close);
    
    wlr_log(WLR_DEBUG, "New foreign toplevel: %s", toplevel->title);
}

void foreign_toplevel_init(struct serein_compositor *comp) {
    comp->foreign_toplevel = wlr_foreign_toplevel_management_v1_create(comp->wl_display);
    
    comp->new_foreign_toplevel.notify = foreign_toplevel_new_handle;
    wl_signal_add(&comp->foreign_toplevel->events.new_toplevel, &comp->new_foreign_toplevel);
}