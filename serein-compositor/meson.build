project('serein-compositor', 'c',
  version: '0.1.0',
  license: 'MIT',
  default_options: [
    'warning_level=2',
    'werror=false',
    'c_std=c11',
    'b_lto=true',
    'b_pie=true',
  ])

wlroots_dep = dependency('wlroots-0.17', version: '>=0.17.0')
wayland_client_dep = dependency('wayland-client', version: '>=1.22.0')
wayland_server_dep = dependency('wayland-server', version: '>=1.22.0')
wayland_protocols_dep = dependency('wayland-protocols', version: '>=1.32', required: false)
wayland_scanner = find_program('wayland-scanner', required: false)

libinput_dep = dependency('libinput', required: false)
libdrm_dep = dependency('libdrm', required: false)
gbm_dep = dependency('gbm', required: false)
seatd_dep = dependency('libseat', required: false)
pipewire_dep = dependency('libpipewire-0.3', required: false)
systemd_dep = dependency('systemd', required: false)
pixman_dep = dependency('pixman-1', version: '>=0.40.0')
cairo_dep = dependency('cairo', required: false)
pango_dep = dependency('pango', required: false)
jsonc_dep = dependency('json-c', required: false)
opengl_dep = dependency('gl', required: false)
glesv2_dep = dependency('glesv2', required: false)
egl_dep = dependency('egl', required: false)

proto_sources = []

if wayland_protocols_dep.found() and wayland_scanner.found()
  wayland_scanner_code = generator(
    wayland_scanner,
    output: '@BASENAME@-protocol.c',
    arguments: ['private-code', '@INPUT@', '@OUTPUT@'],
  )
  wayland_scanner_server = generator(
    wayland_scanner,
    output: '@BASENAME@-protocol.h',
    arguments: ['server-header', '@INPUT@', '@OUTPUT@'],
  )
  
  protocols_dir = wayland_protocols_dep.get_variable('pkgdatadir')
  xdg_shell_proto = protocols_dir + '/stable/xdg-shell/xdg-shell.xml'
  
  proto_sources += wayland_scanner_code.process(xdg_shell_proto)
  proto_sources += wayland_scanner_server.process(xdg_shell_proto)
endif

compositor_sources = files([
  'src/compositor.c',
  'src/output.c',
  'src/input.c',
  'src/layer-shell.c',
  'src/xdg-shell.c',
  'src/foreign-toplevel.c',
  'src/screencopy.c',
  'src/render.c',
  'src/damage.c',
  'src/scene.c',
  'src/avatar-bridge.c',
  'src/config.c',
  'src/cursor-shape.c',
  'src/fractional-scale.c',
  'src/viewporter.c',
  'src/log.c',
]) + proto_sources

compositor_deps = [
  wlroots_dep,
  wayland_client_dep,
  wayland_server_dep,
  pixman_dep,
]

if cairo_dep.found()
  compositor_deps += cairo_dep
endif

if pango_dep.found()
  compositor_deps += pango_dep
endif

if jsonc_dep.found()
  compositor_deps += jsonc_dep
endif

if opengl_dep.found()
  compositor_deps += opengl_dep
endif

if glesv2_dep.found()
  compositor_deps += glesv2_dep
endif

if egl_dep.found()
  compositor_deps += egl_dep
endif

if libinput_dep.found()
  compositor_deps += libinput_dep
endif

if libdrm_dep.found()
  compositor_deps += libdrm_dep
endif

if gbm_dep.found()
  compositor_deps += gbm_dep
endif

if seatd_dep.found()
  compositor_deps += seatd_dep
endif

if pipewire_dep.found()
  compositor_deps += pipewire_dep
endif

if systemd_dep.found()
  compositor_deps += systemd_dep
endif

executable('serein-compositor',
  compositor_sources,
  dependencies: compositor_deps,
  install: true,
  install_dir: '/usr/local/bin',
)

# Generate pkg-config file
pkg = import('pkgconfig')
pkg.generate(
  name: 'serein-compositor',
  description: 'SereinOS Wayland Compositor',
  version: meson.project_version(),
  requires: ['wlroots-0.17', 'wayland-server', 'pixman-1'],
  libs: '-lm',
)

# Install config
install_data('config/serein-compositor.conf',
  install_dir: '/etc/serein/compositor',
  rename: 'config.yaml',
)

# Install session file
install_data('config/serein.desktop',
  install_dir: '/usr/share/wayland-sessions',
)
