#include "compositor.h"
#include <wayland-server-core.h>
#include <wlr/types/wlr_output.h>
#include <wlr/types/wlr_output_layout.h>
#include <wlr/util/log.h>

struct serein_output {
    struct wlr_output *wlr_output;
    struct wl_listener frame;
    struct wl_listener destroy;
    struct wl_list link;
};

static void output_frame_notify(struct wl_listener *listener, void *data) {
    struct serein_output *output = wl_container_of(listener, output, frame);
    struct wlr_output *wlr_output = output->wlr_output;
    
    struct wlr_output_state state;
    wlr_output_state_init(&state);
    wlr_output_state_set_enabled(&state, true);
    
    struct wlr_output_mode *mode = wlr_output_preferred_mode(wlr_output);
    if (mode) {
        wlr_output_state_set_mode(&state, mode);
    }
    
    wlr_output_commit_state(wlr_output, &state);
    wlr_output_state_finish(&state);
}

static void output_destroy_notify(struct wl_listener *listener, void *data) {
    struct serein_output *output = wl_container_of(listener, output, destroy);
    wl_list_remove(&output->frame.link);
    wl_list_remove(&output->destroy.link);
    wl_list_remove(&output->link);
    free(output);
}

static void new_output_notify(struct wl_listener *listener, void *data) {
    struct wlr_output *wlr_output = data;
    
    if (!wlr_output_init_render(wlr_output, compositor.allocator, compositor.renderer)) {
        wlr_log(WLR_ERROR, "Failed to init output render");
        return;
    }
    
    struct serein_output *output = calloc(1, sizeof(struct serein_output));
    output->wlr_output = wlr_output;
    
    output->frame.notify = output_frame_notify;
    wl_signal_add(&wlr_output->events.frame, &output->frame);
    
    output->destroy.notify = output_destroy_notify;
    wl_signal_add(&wlr_output->events.destroy, &output->destroy);
    
    wl_list_insert(&compositor.outputs, &output->link);
    
    wlr_output_layout_add_auto(compositor.output_layout, wlr_output);
    
    wlr_log(WLR_INFO, "New output: %s", wlr_output->name);
}

void output_init(struct serein_compositor *comp) {
    wl_list_init(&comp->outputs);
    
    comp->new_output.notify = new_output_notify;
    wl_signal_add(&comp->backend->events.new_output, &comp->new_output);
}