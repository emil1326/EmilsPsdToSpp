"""Step 2: builds an extracted layer tree in the open Painter project."""
import os

import substance_painter.layerstack as ls
import substance_painter.resource as res
import substance_painter.textureset as ts
from substance_painter.colormanagement import Color

from .direct_masks import MASK_LABEL, PIXELS_LABEL

BASE = ts.ChannelType.BaseColor
RESOURCE_GROUP = "EmilsPsdToSpp"


def _blend(name):
    return getattr(ls.BlendingMode, name, ls.BlendingMode.Normal)


class Builder:
    def __init__(self, folder, progress=None):
        self.folder = folder
        self.progress = progress
        self.cache = {}
        self.count = 0
        self.visibility = []

    def tex(self, filename):
        if filename not in self.cache:
            r = res.import_project_resource(os.path.join(self.folder, filename),
                                            res.Usage.TEXTURE, group=RESOURCE_GROUP)
            self.cache[filename] = r.identifier()
        return self.cache[filename]

    def common(self, node, data):
        node.set_name(data["name"])
        node.set_blending_mode(_blend(data["blend"]), BASE)
        node.set_opacity(float(data["opacity"]), BASE)
        node.set_visible(bool(data["visible"]))
        self.visibility.append((node, bool(data["visible"])))

    def reapply_visibility(self):
        # Painter sometimes drops a child's visibility while its folder is being
        # built, so set them all again once the whole tree exists.
        for node, visible in self.visibility:
            node.set_visible(visible)

    def fill(self, pos, rgb=None, bitmap=None, mask=None):
        if bitmap:
            # too many colors for a flat fill: a paint layer holding the PSD pixels,
            # so you can paint right over them
            fill = ls.insert_paint(pos)
            fill.active_channels = {BASE}
            px = ls.insert_fill(ls.InsertPosition.inside_node(fill, ls.NodeStack.Content))
            px.active_channels = {BASE}
            px.set_source(BASE, self.tex(bitmap))
            px.set_projection_mode(ls.ProjectionMode.UV)
            px.set_name(PIXELS_LABEL)
        else:
            fill = ls.insert_fill(pos)
            fill.active_channels = {BASE}
            fill.set_source(BASE, Color(*(v / 255.0 for v in rgb)))
        if mask:
            fill.add_mask(ls.MaskBackground.Black)
            m = ls.insert_fill(ls.InsertPosition.inside_node(fill, ls.NodeStack.Mask))
            m.set_source(None, self.tex(mask))
            m.set_projection_mode(ls.ProjectionMode.UV)
            m.set_name(MASK_LABEL)
        return fill

    def layer(self, pos, data):
        if self.progress:
            self.progress(data["name"])
        spec = data["fill"]
        if spec["mode"] == "two":
            # gradient between 2 colors: a folder holding both flat colors
            node = ls.insert_group(pos)
            base = self.fill(ls.InsertPosition.inside_node(node, ls.NodeStack.Substack),
                             rgb=spec["base"], mask=data.get("mask"))
            base.set_name(data["name"] + " base")
            top = self.fill(ls.InsertPosition.above_node(base),
                            rgb=spec["top"], mask=data.get("mask2"))
            top.set_name(data["name"] + " gradient")
            node.set_collapsed(True)
        else:
            node = self.fill(pos, rgb=spec.get("color"), bitmap=data.get("bitmap"),
                             mask=data.get("mask"))
        self.common(node, data)
        self.count += 1
        return node

    def group(self, pos, data):
        grp = ls.insert_group(pos)
        self.children(grp, data["children"])
        self.common(grp, data)
        grp.set_collapsed(True)
        return grp

    def children(self, parent, nodes):
        # nodes are bottom -> top: first goes inside, each next one above the previous.
        prev = None
        for data in nodes:
            pos = (ls.InsertPosition.inside_node(parent, ls.NodeStack.Substack)
                   if prev is None else ls.InsertPosition.above_node(prev))
            prev = self.group(pos, data) if data["type"] == "group" else self.layer(pos, data)


def build(manifest, texture_set, replace=False, progress=None):
    """Adds a folder "PSD - <file>" holding the PSD layers on top of the texture set's stack.
    With replace=True every existing layer of that texture set is deleted first."""
    stack = texture_set.get_stack()
    b = Builder(manifest["folder"], progress)
    with ls.ScopedModification("Emil's PSD to Painter"):
        if replace:
            for node in ls.get_root_layer_nodes(stack):
                ls.delete_node(node)
        root = ls.insert_group(ls.InsertPosition.from_textureset_stack(stack))  # top of the stack
        root.set_name("PSD - " + os.path.splitext(manifest["psd"])[0])
        b.children(root, manifest["layers"])
        b.reapply_visibility()
    return b.count
