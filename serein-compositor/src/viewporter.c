#include "compositor.h"
#include <wlr/types/wlr_viewporter.h>
#include <wlr/util/log.h>

static void viewport_destroy_notify(struct wl_listener *listener, void *data) {
    struct wlr_viewport *viewport = data;
    wlr_log(WLR_DEBUG, "Viewport destroyed");
}

static void new_viewport_notify(struct wl_listener *listener, void *data) {
    struct wlr_viewport *viewport = data;
    
    struct wl_listener *destroy = calloc(1, sizeof(struct wl_listener));
    destroy->notify = viewport_destroy_notify;
    wl_signal_add(&viewport->events.destroy, destroy);
    
    wlr_log(WLR_DEBUG, "New viewport: %dx%d -> %dx%d", 
        viewport->source.box.width, viewport->source.box.height,
        viewport->destination.width, viewport->destination.height);
}

void viewporter_init(struct serein_compositor *comp) {
    comp->viewporter = wlr_viewporter_create(comp->wl_display);
    
    comp->new_viewport.notify = new_viewport_notify;
    wl_signal_add(&comp->viewporter->events.new_viewport, &comp->new_viewport);
}