#include "compositor.h"
#include <wlr/render/wlr_renderer.h>
#include <wlr/render/gles2.h>
#include <wlr/util/log.h>

static int g_damage_whole_output = 1;

void render_init(struct serein_compositor *comp) {
    // Initialize renderer
    wlr_renderer_init_wl_display(comp->renderer, comp->wl_display);
    
    // Set up damage tracking for whole outputs initially
    g_damage_whole_output = 1;
    
    wlr_log(WLR_INFO, "Renderer initialized");
}

void render_output(struct wlr_output *output, struct wlr_renderer *renderer, struct wlr_scene *scene) {
    struct wlr_output_state state;
    wlr_output_state_init(&state);
    
    if (g_damage_whole_output) {
        wlr_output_state_set_enabled(&state, true);
    }
    
    struct wlr_scene_output *scene_output = wlr_scene_get_scene_output(scene, output);
    if (!scene_output) {
        wlr_log(WLR_DEBUG, "No scene output for %s", output->name);
        wlr_output_state_finish(&state);
        return;
    }
    
    wlr_scene_output_commit(scene_output, NULL);
    
    struct wlr_renderer *r = wlr_backend_get_renderer(output->backend);
    if (r) {
        wlr_output_attach_render(output, NULL);
        
        int width, height;
        wlr_output_effective_resolution(output, &width, &height);
        wlr_renderer_begin(r, width, height);
        
        wlr_renderer_clear(r, (float[4]){0.1f, 0.1f, 0.12f, 1.0f});
        
        wlr_scene_output_render(scene_output, NULL, NULL);
        
        wlr_renderer_end(r);
        
        wlr_output_attach_render(output, NULL);
        wlr_output_commit_state(output, &state);
    }
    
    wlr_output_state_finish(&state);
}