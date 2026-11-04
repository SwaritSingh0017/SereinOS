#include "compositor.h"
#include <wlr/types/wlr_output.h>
#include <wlr/util/log.h>

struct serein_damage_ring {
    struct wlr_texture *textures[4];
    size_t current;
};

static struct serein_damage_ring damage_ring;

void damage_init(struct serein_compositor *comp) {
    comp->damage_ring = &damage_ring;
    damage_ring.current = 0;
    
    for (size_t i = 0; i < 4; i++) {
        damage_ring.textures[i] = NULL;
    }
    
    wlr_log(WLR_INFO, "Damage ring initialized");
}

void damage_frame_start(struct serein_compositor *comp) {
    // Mark all outputs as damaged for full repaint
    struct serein_output *output;
    wl_list_for_each(output, &comp->outputs, link) {
        wlr_output_damage_add_whole(output->wlr_output);
    }
}

void damage_frame_finish(struct serein_compositor *comp) {
    // Render damaged regions
    struct serein_output *output;
    wl_list_for_each(output, &comp->outputs, link) {
        render_output(output->wlr_output, comp->renderer, comp->scene);
    }
}