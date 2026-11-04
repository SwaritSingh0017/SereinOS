#include "compositor.h"
#include <wlr/scene/wlr_scene.h>
#include <wlr/util/log.h>

struct serein_scene {
    struct wlr_scene_tree *tree;
    struct wlr_scene_rect *background;
    struct wlr_scene_tree *layer_tree[4]; // background, bottom, top, overlay
};

struct serein_scene *scene_create(struct wlr_renderer *renderer, struct wlr_allocator *allocator) {
    struct serein_scene *scene = calloc(1, sizeof(struct serein_scene));
    if (!scene) return NULL;
    
    scene->tree = wlr_scene_tree_create(NULL);
    
    // Create background
    scene->background = wlr_scene_rect_create(scene->tree, 1920, 1080, (float[4]){0.1f, 0.1f, 0.12f, 1.0f});
    
    // Create layer trees
    const char *layer_names[] = {"background", "bottom", "top", "overlay"};
    for (int i = 0; i < 4; i++) {
        scene->layer_tree[i] = wlr_scene_tree_create(scene->tree);
    }
    
    wlr_log(WLR_INFO, "Scene created");
    return scene;
}

void scene_destroy(struct serein_scene *scene) {
    if (scene) {
        wlr_scene_node_destroy(&scene->tree->node);
        free(scene);
    }
}