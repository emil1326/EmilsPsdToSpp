"""Emil's PSD to Painter: imports PSD layers into Substance 3D Painter as
editable fill layers (one flat color + a mask each), with the masks living
directly in the layers.

Menu: File > Import PSD layers (Emil's)...
https://github.com/emil1326/EmilsPsdToSpp
"""


def start_plugin():
    from . import plugin_ui
    plugin_ui.start_plugin()


def close_plugin():
    from . import plugin_ui
    plugin_ui.close_plugin()
