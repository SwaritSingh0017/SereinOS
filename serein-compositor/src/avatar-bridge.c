#include "compositor.h"
#include <wlr/types/wlr_output.h>
#include <wlr/util/log.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

struct serein_avatar_bridge {
    int socket_fd;
    struct sockaddr_in server_addr;
    struct wl_event_source *event_source;
    bool connected;
};

static int avatar_bridge_handle_data(int fd, uint32_t mask, void *data) {
    struct serein_avatar_bridge *bridge = data;
    
    if (mask & WL_EVENT_READABLE) {
        char buffer[4096];
        ssize_t len = recv(fd, buffer, sizeof(buffer) - 1, MSG_DONTWAIT);
        if (len > 0) {
            buffer[len] = '\0';
            wlr_log(WLR_DEBUG, "Avatar bridge received: %s", buffer);
        } else if (len == 0 || (len < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
            wlr_log(WLR_INFO, "Avatar bridge disconnected");
            bridge->connected = false;
            close(fd);
            bridge->socket_fd = -1;
            return 0;
        }
    }
    
    return 1;
}

static void avatar_bridge_connect(struct serein_avatar_bridge *bridge) {
    bridge->socket_fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
    if (bridge->socket_fd < 0) {
        wlr_log(WLR_ERROR, "Failed to create avatar bridge socket");
        return;
    }
    
    bridge->server_addr.sin_family = AF_INET;
    bridge->server_addr.sin_port = htons(compositor.config.avatar_port);
    inet_pton(AF_INET, compositor.config.avatar_host, &bridge->server_addr.sin_addr);
    
    if (connect(bridge->socket_fd, (struct sockaddr *)&bridge->server_addr, sizeof(bridge->server_addr)) < 0) {
        if (errno != EINPROGRESS) {
            wlr_log(WLR_ERROR, "Failed to connect to avatar: %s", strerror(errno));
            close(bridge->socket_fd);
            bridge->socket_fd = -1;
            return;
        }
    }
    
    bridge->event_source = wl_event_loop_add_fd(
        wl_display_get_event_loop(compositor.wl_display),
        bridge->socket_fd,
        WL_EVENT_READABLE | WL_EVENT_WRITABLE,
        avatar_bridge_handle_data,
        bridge
    );
    
    bridge->connected = true;
    wlr_log(WLR_INFO, "Avatar bridge connected to %s:%d", 
        compositor.config.avatar_host, compositor.config.avatar_port);
}

void avatar_bridge_init(struct serein_compositor *comp) {
    if (!comp->config.avatar_enabled) {
        wlr_log(WLR_INFO, "Avatar bridge disabled in config");
        return;
    }
    
    comp->avatar_bridge = calloc(1, sizeof(struct serein_avatar_bridge));
    comp->avatar_bridge->socket_fd = -1;
    comp->avatar_bridge->connected = false;
    
    avatar_bridge_connect(comp->avatar_bridge);
}

void avatar_bridge_send(struct serein_avatar_bridge *bridge, const char *message) {
    if (!bridge || !bridge->connected || bridge->socket_fd < 0) return;
    
    send(bridge->socket_fd, message, strlen(message), MSG_NOSIGNAL);
}

void avatar_bridge_destroy(struct serein_avatar_bridge *bridge) {
    if (!bridge) return;
    
    if (bridge->event_source) {
        wl_event_source_remove(bridge->event_source);
    }
    
    if (bridge->socket_fd >= 0) {
        close(bridge->socket_fd);
    }
    
    free(bridge);
}