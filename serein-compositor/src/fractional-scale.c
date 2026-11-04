#include "compositor.h"
#include <wlr/types/wlr_fractional_scale_v1.h>
#include <wlr/util/log.h>

static void fractional_scale_handle_destroy(struct wl_listener *listener, void *data) {
    struct wlr_fractional_scale_v1 *scale = data;
    wlr_log(WLR_DEBUG, "Fractional scale destroyed");
}

static void new_fractional_scale_notify(struct wl_listener *listener, void *data) {
    struct wlr_fractional_scale_v1 *scale = data;
    
    struct wl_listener *destroy = calloc(1, sizeof(struct wl_listener));
    destroy->notify = fractional_scale_handle_destroy;
    wl_signal_add(&scale->events.destroy, destroy);
    
    wlr_log(WLR_DEBUG, "New fractional scale: %d", scale->preferred_scale);
}

void fractional_scale_init(struct serein_compositor *comp) {
    comp->fractional_scale = wlr_fractional_scale_manager_v1_create(comp->wl_display);
    
    comp->new_fractional_scale.notify = new_fractional_scale_notify;
    wl_signal_add(&comp->fractional_scale->events.new_scale, &comp->new_fractional_scale);
}