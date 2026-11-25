# dmgbuild settings for the macOS install window ("Drag ST-SoftwareTool to Applications").
#
#   dmgbuild -s packaging/macos/dmg_settings.py \
#            -D app=build/ST-SoftwareTool.app -D background=build/dmg-background.tiff \
#            -D icon=build/ST.icns "ST-SoftwareTool" build/ST-SoftwareTool-macOS-arm64.dmg
#
# Icon centres and window size must match make_dmg_background.py (W, H, APP_XY, APPS_XY).
import os.path

application = defines["app"]                       # noqa: F821  (injected by dmgbuild)
appname = os.path.basename(application)

format = "UDZO"
filesystem = "HFS+"
files = [application]
symlinks = {"Applications": "/Applications"}
hide_extensions = [appname]                        # label reads "ST-SoftwareTool", not ".app"

icon = defines.get("icon")                         # noqa: F821  volume icon in Finder / Desktop
background = defines["background"]                 # noqa: F821  1x + 2x combined TIFF

# Window: 800 x 450 background + 28 pt title bar (Finder counts the title bar in the size)
window_rect = ((200, 140), (800, 478))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False
include_icon_view_settings = True

arrange_by = None
icon_size = 128
text_size = 14
label_pos = "bottom"
icon_locations = {
    appname: (190, 195),
    "Applications": (610, 195),
}
