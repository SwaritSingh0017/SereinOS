/*
 * SereinOS Compositor - Logging implementation
 */

#include "log.h"
#include <time.h>
#include <pthread.h>

static serein_log_level_t g_log_level = SEREIN_LOG_INFO;
static pthread_mutex_t log_mutex = PTHREAD_MUTEX_INITIALIZER;

static const char *level_names[] = {
    "DEBUG", "INFO", "WARN", "ERROR"
};

static const char *level_colors[] = {
    "\033[36m",  // DEBUG - cyan
    "\033[32m",  // INFO - green
    "\033[33m",  // WARN - yellow
    "\033[31m",  // ERROR - red
};

static const char *reset_color = "\033[0m";

void serein_log_init(void) {
    // Check environment for log level
    const char *env_level = getenv("SEREIN_LOG_LEVEL");
    if (env_level) {
        if (strcmp(env_level, "debug") == 0) g_log_level = SEREIN_LOG_DEBUG;
        else if (strcmp(env_level, "info") == 0) g_log_level = SEREIN_LOG_INFO;
        else if (strcmp(env_level, "warn") == 0) g_log_level = SEREIN_LOG_WARN;
        else if (strcmp(env_level, "error") == 0) g_log_level = SEREIN_LOG_ERROR;
    }
}

void serein_log_set_level(serein_log_level_t level) {
    g_log_level = level;
}

void serein_log(serein_log_level_t level, const char *file, int line, const char *func, const char *fmt, ...) {
    if (level < g_log_level) return;
    
    pthread_mutex_lock(&log_mutex);
    
    // Get timestamp
    time_t now = time(NULL);
    struct tm *tm_info = localtime(&now);
    char timebuf[32];
    strftime(timebuf, sizeof(timebuf), "%H:%M:%S", tm_info);
    
    // Extract filename from path
    const char *filename = strrchr(file, '/');
    filename = filename ? filename + 1 : file;
    
    // Print to stderr
    fprintf(stderr, "%s%s [%s] %s:%d %s(): ", 
            level_colors[level], timebuf, level_names[level], filename, line, func);
    
    va_list args;
    va_start(args, fmt);
    vfprintf(stderr, fmt, args);
    va_end(args);
    
    fprintf(stderr, "%s\n", reset_color);
    
    pthread_mutex_unlock(&log_mutex);
}