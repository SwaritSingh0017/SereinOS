#include "compositor.h"
#include <yaml.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void config_set_defaults(struct serein_config *config) {
    config->output_count = 0;
    config->keybind_count = 0;
    
    strcpy(config->input.identifier, "*");
    strcpy(config->input.keymap_layout, "us");
    config->input.keymap_variant[0] = '\0';
    config->input.keymap_options[0] = '\0';
    config->input.repeat_rate = 25;
    config->input.repeat_delay = 600;
    config->input.accel_speed = 0.0f;
    config->input.natural_scroll = false;
    
    strcpy(config->cursor_theme, "default");
    config->cursor_size = 24;
    config->cursor_hide_timeout = true;
    config->cursor_hide_seconds = 5;
    
    strcpy(config->avatar_host, "127.0.0.1");
    config->avatar_port = 8765;
    config->avatar_enabled = true;
    
    config->animations_enabled = true;
    config->animation_duration = 0.2f;
    config->gaps_inner = 4;
    config->gaps_outer = 8;
}

static int yaml_read_string(yaml_document_t *doc, yaml_node_t *node, const char *key, char *out, size_t out_size) {
    for (yaml_node_pair_t *pair = node->data.mapping.pairs.start; pair < node->data.mapping.pairs.top; pair++) {
        yaml_node_t *key_node = yaml_document_get_node(doc, pair->key);
        yaml_node_t *value_node = yaml_document_get_node(doc, pair->value);
        
        if (key_node->type == YAML_SCALAR_NODE && strcmp((char *)key_node->data.scalar.value, key) == 0) {
            if (value_node->type == YAML_SCALAR_NODE) {
                strncpy(out, (char *)value_node->data.scalar.value, out_size - 1);
                out[out_size - 1] = '\0';
                return 1;
            }
        }
    }
    return 0;
}

static int yaml_read_int(yaml_document_t *doc, yaml_node_t *node, const char *key, int *out) {
    char buf[32];
    if (yaml_read_string(doc, node, key, buf, sizeof(buf))) {
        *out = atoi(buf);
        return 1;
    }
    return 0;
}

static int yaml_read_float(yaml_document_t *doc, yaml_node_t *node, const char *key, float *out) {
    char buf[32];
    if (yaml_read_string(doc, node, key, buf, sizeof(buf))) {
        *out = strtof(buf, NULL);
        return 1;
    }
    return 0;
}

static int yaml_read_bool(yaml_document_t *doc, yaml_node_t *node, const char *key, bool *out) {
    char buf[32];
    if (yaml_read_string(doc, node, key, buf, sizeof(buf))) {
        *out = (strcmp(buf, "true") == 0 || strcmp(buf, "yes") == 0 || strcmp(buf, "1") == 0);
        return 1;
    }
    return 0;
}

int config_load(const char *path, struct serein_config *config) {
    config_set_defaults(config);
    
    FILE *file = fopen(path, "r");
    if (!file) {
        return 0; // Use defaults
    }
    
    yaml_parser_t parser;
    yaml_document_t document;
    
    yaml_parser_initialize(&parser);
    yaml_parser_set_input_file(&parser, file);
    
    if (!yaml_parser_load(&parser, &document)) {
        yaml_parser_delete(&parser);
        fclose(file);
        return -1;
    }
    
    yaml_node_t *root = yaml_document_get_root_node(&document);
    if (!root || root->type != YAML_MAPPING_NODE) {
        yaml_document_delete(&document);
        yaml_parser_delete(&parser);
        fclose(file);
        return -1;
    }
    
    for (yaml_node_pair_t *pair = root->data.mapping.pairs.start; pair < root->data.mapping.pairs.top; pair++) {
        yaml_node_t *key_node = yaml_document_get_node(&document, pair->key);
        yaml_node_t *value_node = yaml_document_get_node(&document, pair->value);
        
        if (key_node->type != YAML_SCALAR_NODE) continue;
        
        const char *key = (char *)key_node->data.scalar.value;
        
        if (strcmp(key, "input") == 0 && value_node->type == YAML_MAPPING_NODE) {
            yaml_read_string(&document, value_node, "identifier", config->input.identifier, sizeof(config->input.identifier));
            yaml_read_string(&document, value_node, "keymap_layout", config->input.keymap_layout, sizeof(config->input.keymap_layout));
            yaml_read_string(&document, value_node, "keymap_variant", config->input.keymap_variant, sizeof(config->input.keymap_variant));
            yaml_read_string(&document, value_node, "keymap_options", config->input.keymap_options, sizeof(config->input.keymap_options));
            yaml_read_int(&document, value_node, "repeat_rate", &config->input.repeat_rate);
            yaml_read_int(&document, value_node, "repeat_delay", &config->input.repeat_delay);
            yaml_read_float(&document, value_node, "accel_speed", &config->input.accel_speed);
            yaml_read_bool(&document, value_node, "natural_scroll", &config->input.natural_scroll);
        }
        else if (strcmp(key, "cursor") == 0 && value_node->type == YAML_MAPPING_NODE) {
            yaml_read_string(&document, value_node, "theme", config->cursor_theme, sizeof(config->cursor_theme));
            yaml_read_int(&document, value_node, "size", &config->cursor_size);
            yaml_read_bool(&document, value_node, "hide_timeout", &config->cursor_hide_timeout);
            yaml_read_int(&document, value_node, "hide_seconds", &config->cursor_hide_seconds);
        }
        else if (strcmp(key, "avatar") == 0 && value_node->type == YAML_MAPPING_NODE) {
            yaml_read_string(&document, value_node, "host", config->avatar_host, sizeof(config->avatar_host));
            yaml_read_int(&document, value_node, "port", &config->avatar_port);
            yaml_read_bool(&document, value_node, "enabled", &config->avatar_enabled);
        }
        else if (strcmp(key, "animations") == 0 && value_node->type == YAML_MAPPING_NODE) {
            yaml_read_bool(&document, value_node, "enabled", &config->animations_enabled);
            yaml_read_float(&document, value_node, "duration", &config->animation_duration);
        }
        else if (strcmp(key, "gaps") == 0 && value_node->type == YAML_MAPPING_NODE) {
            yaml_read_int(&document, value_node, "inner", &config->gaps_inner);
            yaml_read_int(&document, value_node, "outer", &config->gaps_outer);
        }
        else if (strcmp(key, "outputs") == 0 && value_node->type == YAML_SEQUENCE_NODE) {
            for (yaml_node_item_t *item = value_node->data.sequence.items.start; item < value_node->data.sequence.items.top; item++) {
                yaml_node_t *output_node = yaml_document_get_node(&document, *item);
                if (output_node->type == YAML_MAPPING_NODE && config->output_count < SEREIN_CONFIG_MAX_OUTPUTS) {
                    struct serein_output_config *out = &config->outputs[config->output_count++];
                    yaml_read_string(&document, output_node, "name", out->name, sizeof(out->name));
                    yaml_read_int(&document, output_node, "x", &out->x);
                    yaml_read_int(&document, output_node, "y", &out->y);
                    yaml_read_int(&document, output_node, "width", &out->width);
                    yaml_read_int(&document, output_node, "height", &out->height);
                    yaml_read_float(&document, output_node, "scale", &out->scale);
                    yaml_read_int(&document, output_node, "transform", &out->transform);
                    yaml_read_bool(&document, output_node, "enabled", &out->enabled);
                }
            }
        }
    }
    
    yaml_document_delete(&document);
    yaml_parser_delete(&parser);
    fclose(file);
    
    return 0;
}