# SereinOS Compositor - Wayland compositor using wlroots
# This is a CMake project for building a custom Wayland compositor
# that runs the Serein avatar as the primary full-screen surface.

cmake_minimum_required(VERSION 3.16)

project(serein-compositor VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

# Build options
option(BUILD_WAYLAND "Build with Wayland support" ON)
option(BUILD_XWAYLAND "Build with XWayland support" ON)
option(BUILD_DEBUG "Build with debug symbols" ON)
option(ENABLE_SANITIZERS "Enable ASan/UBSan" OFF)

# Compiler flags
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wall -Wextra -Wpedantic")
if(BUILD_DEBUG)
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -g -O0")
else()
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -O2 -DNDEBUG")
endif()

if(ENABLE_SANITIZERS)
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -fsanitize=address,undefined -fno-omit-frame-pointer")
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -fsanitize=address,undefined")
endif()

# Dependencies
find_package(PkgConfig REQUIRED)
pkg_check_modules(WAYLAND_SERVER REQUIRED wayland-server>=1.20)
pkg_check_modules(WAYLAND_CLIENT REQUIRED wayland-client>=1.20)
pkg_check_modules(WLR REQUIRED wlroots-0.17>=0.17.0)
pkg_check_modules(PIXMAN REQUIRED pixman-1>=0.40.0)

pkg_check_modules(JSONC json-c)
pkg_check_modules(LIBDRM libdrm)
pkg_check_modules(GBM gbm)
pkg_check_modules(EGL egl)
pkg_check_modules(GLES2 glesv2)

# Include directories
include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${WAYLAND_SERVER_INCLUDE_DIRS}
    ${WAYLAND_CLIENT_INCLUDE_DIRS}
    ${WLR_INCLUDE_DIRS}
    ${PIXMAN_INCLUDE_DIRS}
    ${JSONC_INCLUDE_DIRS}
    ${LIBDRM_INCLUDE_DIRS}
    ${GBM_INCLUDE_DIRS}
    ${EGL_INCLUDE_DIRS}
    ${GLES2_INCLUDE_DIRS}
)

# Source files
set(SOURCES
    src/compositor.c
    src/output.c
    src/input.c
    src/layer-shell.c
    src/xdg-shell.c
    src/foreign-toplevel.c
    src/screencopy.c
    src/render.c
    src/damage.c
    src/scene.c
    src/avatar-bridge.c
    src/config.c
    src/cursor-shape.c
    src/fractional-scale.c
    src/viewporter.c
    src/log.c
)

# Executable
add_executable(serein-compositor ${SOURCES})

target_link_libraries(serein-compositor PRIVATE
    ${WAYLAND_SERVER_LIBRARIES}
    ${WAYLAND_CLIENT_LIBRARIES}
    ${WLR_LIBRARIES}
    ${PIXMAN_LIBRARIES}
    ${JSONC_LIBRARIES}
    ${LIBDRM_LIBRARIES}
    ${GBM_LIBRARIES}
    ${EGL_LIBRARIES}
    ${GLES2_LIBRARIES}
    m
)

# Install
install(TARGETS serein-compositor DESTINATION /usr/local/bin)
install(FILES config/serein-compositor.conf DESTINATION /etc/serein/compositor RENAME config.yaml)
install(FILES config/serein.desktop DESTINATION /usr/share/wayland-sessions)
