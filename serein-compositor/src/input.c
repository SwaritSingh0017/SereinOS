#include "compositor.h"
#include <wayland-server-core.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_pointer.h>
#include <wlr/types/wlr_touch.h>
#include <wlr/types/wlr_cursor.h>
#include <wlr/types/wlr_xcursor_manager.h>
#include <wlr/util/log.h>

struct serein_keyboard {
    struct wlr_keyboard *wlr_keyboard;
    struct wl_listener modifiers;
    struct wl_listener key;
    struct wl_listener destroy;
    struct wl_list link;
};

struct serein_pointer {
    struct wlr_pointer *wlr_pointer;
    struct wl_listener motion;
    struct wl_listener motion_absolute;
    struct wl_listener button;
    struct wl_listener axis;
    struct wl_listener frame;
    struct wl_listener destroy;
    struct wl_list link;
};

struct serein_touch {
    struct wlr_touch *wlr_touch;
    struct wl_listener down;
    struct wl_listener up;
    struct wl_listener motion;
    struct wl_listener cancel;
    struct wl_listener destroy;
    struct wl_list link;
};

static void keyboard_modifiers_notify(struct wl_listener *listener, void *data) {
    struct serein_keyboard *keyboard = wl_container_of(listener, keyboard, modifiers);
    wlr_seat_set_keyboard(compositor.seat, keyboard->wlr_keyboard);
    wlr_seat_keyboard_notify_modifiers(compositor.seat, &keyboard->wlr_keyboard->modifiers);
}

static void keyboard_key_notify(struct wl_listener *listener, void *data) {
    struct serein_keyboard *keyboard = wl_container_of(listener, keyboard, key);
    struct wlr_keyboard_key_event *event = data;
    wlr_seat_set_keyboard(compositor.seat, keyboard->wlr_keyboard);
    wlr_seat_keyboard_notify_key(compositor.seat, event->time_msec, event->keycode, event->state);
}

static void keyboard_destroy_notify(struct wl_listener *listener, void *data) {
    struct serein_keyboard *keyboard = wl_container_of(listener, keyboard, destroy);
    wl_list_remove(&keyboard->modifiers.link);
    wl_list_remove(&keyboard->key.link);
    wl_list_remove(&keyboard->destroy.link);
    wl_list_remove(&keyboard->link);
    free(keyboard);
}

static void pointer_motion_notify(struct wl_listener *listener, void *data) {
    struct serein_pointer *pointer = wl_container_of(listener, pointer, motion);
    struct wlr_pointer_motion_event *event = data;
    wlr_cursor_move(pointer->wlr_pointer->cursor, &pointer->wlr_pointer->base, event->delta_x, event->delta_y);
}

static void pointer_motion_absolute_notify(struct wl_listener *listener, void *data) {
    struct serein_pointer *pointer = wl_container_of(listener, pointer, motion_absolute);
    struct wlr_pointer_motion_absolute_event *event = data;
    wlr_cursor_warp_absolute(pointer->wlr_pointer->cursor, &pointer->wlr_pointer->base, event->x, event->y);
}

static void pointer_button_notify(struct wl_listener *listener, void *data) {
    struct serein_pointer *pointer = wl_container_of(listener, pointer, button);
    struct wlr_pointer_button_event *event = data;
    wlr_seat_pointer_notify_button(compositor.seat, event->time_msec, event->button, event->state);
}

static void pointer_axis_notify(struct wl_listener *listener, void *data) {
    struct serein_pointer *pointer = wl_container_of(listener, pointer, axis);
    struct wlr_pointer_axis_event *event = data;
    wlr_seat_pointer_notify_axis(compositor.seat, event->time_msec, event->orientation, event->delta, event->delta_discrete, event->source, event->relative_direction);
}

static void pointer_frame_notify(struct wl_listener *listener, void *data) {
    wlr_seat_pointer_notify_frame(compositor.seat);
}

static void pointer_destroy_notify(struct wl_listener *listener, void *data) {
    struct serein_pointer *pointer = wl_container_of(listener, pointer, destroy);
    wl_list_remove(&pointer->motion.link);
    wl_list_remove(&pointer->motion_absolute.link);
    wl_list_remove(&pointer->button.link);
    wl_list_remove(&pointer->axis.link);
    wl_list_remove(&pointer->frame.link);
    wl_list_remove(&pointer->destroy.link);
    wl_list_remove(&pointer->link);
    free(pointer);
}

static void touch_down_notify(struct wl_listener *listener, void *data) {
    struct serein_touch *touch = wl_container_of(listener, touch, down);
    struct wlr_touch_down_event *event = data;
    wlr_seat_touch_notify_down(compositor.seat, event->time_msec, event->touch_id, event->x, event->y);
}

static void touch_up_notify(struct wl_listener *listener, void *data) {
    struct serein_touch *touch = wl_container_of(listener, touch, up);
    struct wlr_touch_up_event *event = data;
    wlr_seat_touch_notify_up(compositor.seat, event->time_msec, event->touch_id);
}

static void touch_motion_notify(struct wl_listener *listener, void *data) {
    struct serein_touch *touch = wl_container_of(listener, touch, motion);
    struct wlr_touch_motion_event *event = data;
    wlr_seat_touch_notify_motion(compositor.seat, event->time_msec, event->touch_id, event->x, event->y);
}

static void touch_cancel_notify(struct wl_listener *listener, void *data) {
    struct serein_touch *touch = wl_container_of(listener, touch, cancel);
    wlr_seat_touch_notify_cancel(compositor.seat);
}

static void touch_destroy_notify(struct wl_listener *listener, void *data) {
    struct serein_touch *touch = wl_container_of(listener, touch, destroy);
    wl_list_remove(&touch->down.link);
    wl_list_remove(&touch->up.link);
    wl_list_remove(&touch->motion.link);
    wl_list_remove(&touch->cancel.link);
    wl_list_remove(&touch->destroy.link);
    wl_list_remove(&touch->link);
    free(touch);
}

static void new_input_notify(struct wl_listener *listener, void *data) {
    struct wlr_input_device *device = data;
    
    switch (device->type) {
        case WLR_INPUT_DEVICE_KEYBOARD: {
            struct serein_keyboard *keyboard = calloc(1, sizeof(struct serein_keyboard));
            keyboard->wlr_keyboard = wlr_keyboard_from_input_device(device);
            
            keyboard->modifiers.notify = keyboard_modifiers_notify;
            wl_signal_add(&keyboard->wlr_keyboard->events.modifiers, &keyboard->modifiers);
            
            keyboard->key.notify = keyboard_key_notify;
            wl_signal_add(&keyboard->wlr_keyboard->events.key, &keyboard->key);
            
            keyboard->destroy.notify = keyboard_destroy_notify;
            wl_signal_add(&device->events.destroy, &keyboard->destroy);
            
            wl_list_insert(&compositor.keyboards, &keyboard->link);
            
            struct wlr_xcursor_manager *xcursor_manager = wlr_xcursor_manager_create(compositor.config.cursor_theme, compositor.config.cursor_size);
            if (xcursor_manager) {
                wlr_xcursor_manager_load(xcursor_manager, 1);
            }
            break;
        }
        case WLR_INPUT_DEVICE_POINTER: {
            struct serein_pointer *pointer = calloc(1, sizeof(struct serein_pointer));
            pointer->wlr_pointer = wlr_pointer_from_input_device(device);
            
            pointer->motion.notify = pointer_motion_notify;
            wl_signal_add(&pointer->wlr_pointer->events.motion, &pointer->motion);
            
            pointer->motion_absolute.notify = pointer_motion_absolute_notify;
            wl_signal_add(&pointer->wlr_pointer->events.motion_absolute, &pointer->motion_absolute);
            
            pointer->button.notify = pointer_button_notify;
            wl_signal_add(&pointer->wlr_pointer->events.button, &pointer->button);
            
            pointer->axis.notify = pointer_axis_notify;
            wl_signal_add(&pointer->wlr_pointer->events.axis, &pointer->axis);
            
            pointer->frame.notify = pointer_frame_notify;
            wl_signal_add(&pointer->wlr_pointer->events.frame, &pointer->frame);
            
            pointer->destroy.notify = pointer_destroy_notify;
            wl_signal_add(&device->events.destroy, &pointer->destroy);
            
            wl_list_insert(&compositor.pointers, &pointer->link);
            
            wlr_cursor_attach_input_device(compositor.seat->cursor, &device->base);
            break;
        }
        case WLR_INPUT_DEVICE_TOUCH: {
            struct serein_touch *touch = calloc(1, sizeof(struct serein_touch));
            touch->wlr_touch = wlr_touch_from_input_device(device);
            
            touch->down.notify = touch_down_notify;
            wl_signal_add(&touch->wlr_touch->events.down, &touch->down);
            
            touch->up.notify = touch_up_notify;
            wl_signal_add(&touch->wlr_touch->events.up, &touch->up);
            
            touch->motion.notify = touch_motion_notify;
            wl_signal_add(&touch->wlr_touch->events.motion, &touch->motion);
            
            touch->cancel.notify = touch_cancel_notify;
            wl_signal_add(&touch->wlr_touch->events.cancel, &touch->cancel);
            
            touch->destroy.notify = touch_destroy_notify;
            wl_signal_add(&device->events.destroy, &touch->destroy);
            
            wl_list_insert(&compositor.touches, &touch->link);
            break;
        }
        default:
            break;
    }
    
    uint32_t caps = WL_SEAT_CAPABILITY_POINTER;
    if (!wl_list_empty(&compositor.keyboards)) caps |= WL_SEAT_CAPABILITY_KEYBOARD;
    if (!wl_list_empty(&compositor.touches)) caps |= WL_SEAT_CAPABILITY_TOUCH;
    wlr_seat_set_capabilities(compositor.seat, caps);
}

static void request_cursor_notify(struct wl_listener *listener, void *data) {
    struct wlr_seat_pointer_request_set_cursor_event *event = data;
    struct wlr_seat_client *client = event->seat_client;
    struct wlr_surface *surface = event->surface;
    
    if (event->serial) {
        wlr_cursor_set_surface(compositor.seat->cursor, surface, event->hotspot_x, event->hotspot_y);
    }
}

void input_init(struct serein_compositor *comp) {
    wl_list_init(&comp->keyboards);
    wl_list_init(&comp->pointers);
    wl_list_init(&comp->touches);
    
    comp->seat = wlr_seat_create(comp->wl_display, "seat0");
    
    comp->new_input.notify = new_input_notify;
    wl_signal_add(&comp->backend->events.new_input, &comp->new_input);
    
    comp->request_cursor.notify = request_cursor_notify;
    wl_signal_add(&comp->seat->events.request_set_cursor, &comp->request_cursor);
    
    comp->seat->cursor = wlr_cursor_create();
    wlr_cursor_attach_output_layout(comp->seat->cursor, comp->output_layout);
    
    struct wlr_xcursor_manager *xcursor_manager = wlr_xcursor_manager_create(comp->config.cursor_theme, comp->config.cursor_size);
    if (xcursor_manager) {
        wlr_xcursor_manager_load(xcursor_manager, 1);
    }
}