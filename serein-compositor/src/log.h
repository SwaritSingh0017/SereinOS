/*
 * SereinOS Compositor - Logging
 */

#ifndef SEREIN_LOG_H
#define SEREIN_LOG_H

#include <stdarg.h>
#include <stdio.h>

typedef enum {
    SEREIN_LOG_DEBUG = 0,
    SEREIN_LOG_INFO = 1,
    SEREIN_LOG_WARN = 2,
    SEREIN_LOG_ERROR = 3,
} serein_log_level_t;

extern serein_log_level_t g_log_level;

void serein_log_init(void);
void serein_log_set_level(serein_log_level_t level);

#define serein_log_debug(fmt, ...) \
    do { if (g_log_level <= SEREIN_LOG_DEBUG) serein_log(SEREIN_LOG_DEBUG, __FILE__, __LINE__, __func__, fmt, ##__VA_ARGS__); } while(0)

#define serein_log_info(fmt, ...) \
    do { if (g_log_level <= SEREIN_LOG_INFO) serein_log(SEREIN_LOG_INFO, __FILE__, __LINE__, __func__, fmt, ##__VA_ARGS__); } while(0)

#define serein_log_warn(fmt, ...) \
    do { if (g_log_level <= SEREIN_LOG_WARN) serein_log(SEREIN_LOG_WARN, __FILE__, __LINE__, __func__, fmt, ##__VA_ARGS__); } while(0)

#define serein_log_error(fmt, ...) \
    do { if (g_log_level <= SEREIN_LOG_ERROR) serein_log(SEREIN_LOG_ERROR, __FILE__, __LINE__, __func__, fmt, ##__VA_ARGS__); } while(0)

void serein_log(serein_log_level_t level, const char *file, int line, const char *func, const char *fmt, ...);

#endif