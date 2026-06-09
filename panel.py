"""
Panel UI module for Auto Node Runner.

Contains the Auto Texture panel and all drawing logic.
Separated from operators.py for clean module separation.

Copyright (C) 2026 Auto Texture contributors
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This file is part of the Auto Texture extension to Node Runner.
Node Runner is originally by Noah Thiering, licensed under GPL-3.0.
"""

import os

import bpy

from . import i18n
from . import history

_PKG = __package__.rpartition(".")[2] if "." in __package__ else __package__

# [Auto Texture] Texture type tuples without AO, used for missing count (AO excluded) - Modified: 2026-06-09
_TEXTURE_TYPES_NO_AO = (
    ("basecolor_path", "label.basecolor", "IMAGE_RGB"),
    ("metallic_path", "label.metallic", "IMAGE_RGB"),
    ("roughness_path", "label.roughness", "IMAGE_RGB"),
    ("normal_path", "label.normal", "IMAGE_RGB"),
)

# [Auto Texture] Full texture type tuples including AO (AO excluded from missing count) - Modified: 2026-06-09
_TEXTURE_TYPES = _TEXTURE_TYPES_NO_AO + (
    ("ao_path", "label.ao", "IMAGE_RGB"),
)

_HISTORY_LOADED_FLAG = "_auto_texture_history_loaded"


# [Auto Texture] Collect unique materials from selected mesh objects - Modified: 2026-06-09
def _get_unique_materials_from_selection(context):
    materials = []
    seen = set()
    for obj in context.selected_objects:
        if obj.type == "MESH":
            for slot in obj.material_slots:
                if slot.material and slot.material.name not in seen:
                    seen.add(slot.material.name)
                    materials.append(slot.material)
    return materials


# [Auto Texture] Count missing texture paths (AO excluded, counts empty or non-existent paths) - Modified: 2026-06-09
def _count_missing(item):
    missing = 0
    for attr, _, _ in _TEXTURE_TYPES_NO_AO:
        path = getattr(item, attr, "")
        if not path or not os.path.exists(path):
            missing += 1
    return missing


# [Auto Texture] Draw the resource directory selection row - Modified: 2026-06-09
def _draw_directory_row(layout, node_runner):
    row = layout.row(align=True)
    row.label(text=i18n.get_text("label.resource_directory"), icon="FILE_FOLDER")
    row.prop(node_runner, "texture_directory", text="")


# [Auto Texture] Draw the match/clear textures button row - Modified: 2026-06-09
def _draw_match_button_row(layout, mat_count):
    match_id = _PKG + ".match_textures"
    clear_id = _PKG + ".clear_texture_matches"
    row = layout.row(align=True)
    row.operator(
        match_id,
        text=i18n.get_text("button.match_textures", count=mat_count),
        icon="VIEWZOOM",
    )
    row.operator(clear_id, text="", icon="X")


# [Auto Texture] Draw a single texture path row with missing-file alert - Modified: 2026-06-09
def _draw_texture_row(box, item, tex_attr, label_key, icon_name):
    row = box.row(align=True)
    row.label(text=i18n.get_text(label_key) + ":", icon=icon_name)
    path = getattr(item, tex_attr, "")
    if path and not os.path.exists(path):
        row.alert = True
    row.prop(item, tex_attr, text="", emboss=True)


# [Auto Texture] Draw a material block with all texture rows - Modified: 2026-06-09
def _draw_material_block(layout, item):
    box = layout.box()
    box.label(text=item.material_name, icon="MATERIAL")

    not_found_text = i18n.get_text("message.texture_not_found")
    for tex_attr, label_key, icon_name in _TEXTURE_TYPES:
        _draw_texture_row(box, item, tex_attr, label_key, icon_name)


# [Auto Texture] Draw the apply-all button row with missing count - Modified: 2026-06-09
def _draw_apply_button_row(layout, missing_count):
    apply_id = _PKG + ".apply_textures"
    row = layout.row()
    row.operator(
        apply_id,
        text=i18n.get_text("button.apply_all", count=missing_count),
        icon="PLAY",
    )


# [Auto Texture] VIEW_3D panel class for Auto Texture UI - Modified: 2026-06-09
class NODE_RUNNER_PT_auto_texture(bpy.types.Panel):
    bl_label = "Auto Texture"
    bl_idname = _PKG + "_panel_auto_texture"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Auto Texture"

    @classmethod
    def poll(cls, context):
        return True

    # [Auto Texture] Draw method: auto-set directory, load history, apply button before material list - Modified: 2026-06-09
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        node_runner = scene.node_runner

        self.__class__.bl_label = i18n.get_text("panel.title")
        self.__class__.bl_category = i18n.get_text("panel.category")

        # Auto-set resource directory to blend file directory if not set
        # Modified: 2026-06-09
        if not node_runner.texture_directory and bpy.data.filepath:
            blend_dir = os.path.dirname(bpy.data.filepath)
            if blend_dir:
                node_runner.texture_directory = blend_dir

        _draw_directory_row(layout, node_runner)

        materials = _get_unique_materials_from_selection(context)
        mat_count = len(materials)
        _draw_match_button_row(layout, mat_count)

        if not node_runner.texture_matches:
            history_data = history.load_texture_matches(context)
            if history_data:
                hist_dir = history_data.get("texture_directory", "")
                if hist_dir:
                    node_runner.texture_directory = hist_dir
                for match in history_data.get("matches", []):
                    mat_name = match.get("material_name", "")
                    if not mat_name:
                        continue
                    item = node_runner.texture_matches.add()
                    item.material_name = mat_name
                    for tex_attr in ("basecolor_path", "metallic_path", "roughness_path", "normal_path", "ao_path"):
                        val = match.get(tex_attr, "")
                        if val and history.validate_file_path(val):
                            setattr(item, tex_attr, val)
                        else:
                            setattr(item, tex_attr, val if val else "")

        if node_runner.texture_matches:
            missing = 0
            for item in node_runner.texture_matches:
                missing += _count_missing(item)

            _draw_apply_button_row(layout, missing)

            layout.separator()
            for item in node_runner.texture_matches:
                _draw_material_block(layout, item)


_classes = (
    NODE_RUNNER_PT_auto_texture,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
