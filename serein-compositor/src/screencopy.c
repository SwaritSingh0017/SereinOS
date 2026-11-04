#include "compositor.h"
#include <wlr/types/wlr_screencopy_v1.h>
#include <wlr/util/log.h>

static void screencopy_frame_handle_buffer(struct wl_listener *listener, void *data) {
    struct wlr_screencopy_frame_v1 *frame = data;
    wlr_log(WLR_DEBUG, "Screencopy frame buffer ready");
}

static void screencopy_frame_handle_destroy(struct wl_listener *listener, void *data) {
    struct wlr_screencopy_frame_v1 *frame = data;
    wlr_log(WLR_DEBUG, "Screencopy frame destroyed");
}

static void screencopy_handle_copy_output(struct wl_listener *listener, void *data) {
    struct wlr_screencopy_manager_v1_copy_output_event *event = data;
    wlr_log(WLR_DEBUG, "Screencopy output copy requested");
    
    struct wlr_output *output = event->output;
    struct wlr_screencopy_frame_v1 *frame = wlr_screencopy_frame_v1_create(
        compositor.screencopy, event->client, output,
        event->overlay_cursor, event->source_box
    );
    
    struct wl_listener *buffer = calloc(1, sizeof(struct wl_listener));
    buffer->notify = screencopy_frame_handle_buffer;
    wl_signal_add(&frame->events.buffer, buffer);
    
    struct wl_listener *destroy = calloc(1, sizeof(struct wl_listener));
    destroy->notify = screencopy_frame_handle_destroy;
    wl_signal_add(&frame->events.destroy, destroy);
}

static void screencopy_handle_copy_region(struct wl_listener *listener, void *data) {
    struct wlr_screencopy_manager_v1_copy_region_event *event = data;
    wlr_log(WLR_DEBUG, "Screencopy region copy requested");
    
    struct wlr_screencopy_frame_v1 *frame = wlr_screencopy_frame_v1_create_region(
        compositor.screencopy, event->client, event->output,
        event->overlay_cursor, event->region
    );
    
    struct wl_listener *buffer = calloc(1, sizeof(struct wl_listener));
    buffer->notify = screencopy_frame_handle_buffer;
    wl_signal_add(&frame->events.buffer, buffer);
    
    struct wl_listener *destroy = calloc(1, sizeof(struct wl_listener));
    destroy->notify = screencopy_frame_handle_destroy;
    wl_signal_add(&frame->events.destroy, destroy);
}

void screencopy_init(struct serein_compositor *comp) {
    comp->screencopy = wlr_screencopy_manager_v1_create(comp->wl_display);
    
    comp->copy_output.notify = screencopy_handle_copy_output;
    wl_signal_add(&comp->screencopy->events.copy_output, &comp->copy_output);
    
    comp->copy_region.notify = screencopy_handle_copy_region;
    wl_signal_add(&comp->screencopy->events.copy_region, &comp->copy_region);
}