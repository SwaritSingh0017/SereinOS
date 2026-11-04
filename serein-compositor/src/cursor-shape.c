#include "compositor.h"
#include <wlr/types/wlr_cursor_shape_v1.h>
#include <wlr/util/log.h>

static void cursor_shape_handle_set_shape(struct wl_listener *listener, void *data) {
    struct wlr_cursor_shape_manager_v1_set_shape_event *event = data;
    wlr_log(WLR_DEBUG, "Cursor shape set: %s", event->shape);
    
    struct wlr_cursor *cursor = compositor.seat->cursor;
    if (cursor) {
        // wlr_cursor_set_xcursor doesn't exist, we'd need to use wlr_xcursor_manager
        // For now just log
    }
}

void cursor_shape_init(struct serein_compositor *comp) {
    comp->cursor_shape = wlr_cursor_shape_manager_v1_create(comp->wl_display);
    
    comp->set_cursor_shape.notify = cursor_shape_handle_set_shape;
    wl_signal_add(&comp->cursor_shape->events.set_shape, &comp->set_cursor_shape);
}