"""
History persistence module for Auto Node Runner.

Manages reading/writing nodetmp.txt for texture match persistence,
and merging auto-matched results with old history records.

Copyright (C) 2026 Auto Texture contributors
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This file is part of the Auto Texture extension to Node Runner.
Node Runner is originally by Noah Thiering, licensed under GPL-3.0.
"""

import logging
import os
import json
import tempfile

import bpy

log = logging.getLogger(__name__)

# [Auto Texture] Filename constant for the nodetmp persistence file - Modified: 2026-06-09
_NODETMP_FILENAME = "nodetmp.txt"
# [Auto Texture] Save format version for forward/backward compatibility - Modified: 2026-06-09
_SAVE_VERSION = 1
# [Auto Texture] All 9 texture path attribute names - Modified: 2026-08-16
_TEX_ATTRS = (
    "basecolor_path", "metallic_path", "roughness_path", "normal_path",
    "ao_path", "alpha_path", "displacement_path", "specular_path", "emission_path",
)


# [Auto Texture] Resolve nodetmp path from blend file dir, fallback to tempdir - Modified: 2026-06-09
def get_nodetmp_path(context=None):
    if context is not None and hasattr(context, "blend_data") and context.blend_data.filepath:
        return os.path.join(os.path.dirname(context.blend_data.filepath), _NODETMP_FILENAME)
    if bpy.data.filepath:
        return os.path.join(os.path.dirname(bpy.data.filepath), _NODETMP_FILENAME)
    return os.path.join(tempfile.gettempdir(), _NODETMP_FILENAME)


# [Auto Texture] Save texture matches to nodetmp file using atomic write - Modified: 2026-06-09
def save_texture_matches(context):
    scene = context.scene
    node_runner = scene.node_runner

    data = {
        "version": _SAVE_VERSION,
        "texture_directory": node_runner.texture_directory,
        "matches": [],
    }

    for item in node_runner.texture_matches:
        record = {"material_name": item.material_name}
        for attr in _TEX_ATTRS:
            record[attr] = getattr(item, attr, "")
        data["matches"].append(record)

    tmp_path = get_nodetmp_path(context)
    #print(f"[AutoTexture] save_nodetmp: path={tmp_path}, matches={len(data['matches'])}")

    try:
        tmp_file = tmp_path + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, tmp_path)
        #print(f"[AutoTexture] save_nodetmp: saved successfully")
    except Exception as e:
        print(f"[AutoTexture] save_nodetmp: FAILED: {e}")
        log.warning(f"Failed to save nodetmp.txt: {e}")


# [Auto Texture] Load texture matches from nodetmp file with format validation - Modified: 2026-06-09
def load_texture_matches(context):
    tmp_path = get_nodetmp_path(context)
    if not tmp_path or not os.path.exists(tmp_path):
        return None

    try:
        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.warning(f"Failed to load nodetmp.txt: {e}")
        return None

    if not isinstance(data, dict) or "matches" not in data:
        log.warning("nodetmp.txt format invalid: missing 'matches' key")
        return None

    return data


# [Auto Texture] Validate that a file path exists and is a regular file - Modified: 2026-06-09
def validate_file_path(path):
    if not path:
        return False
    try:
        return os.path.isfile(path)
    except (OSError, ValueError, TypeError):
        return False


# [Auto Texture] Merge auto-matched results with history; auto-match takes priority - Modified: 2026-06-09
def merge_with_history(auto_matches, history_data, current_materials):
    if not history_data or "matches" not in history_data:
        return auto_matches

    history_by_mat = {}
    for match in history_data["matches"]:
        mat_name = match.get("material_name", "")
        if mat_name in current_materials:
            history_by_mat[mat_name] = match

    result = dict(auto_matches)

    for mat_name in current_materials:
        if mat_name in result:
            if mat_name not in history_by_mat:
                continue
            hist = history_by_mat[mat_name]
            for tex_type in _TEX_ATTRS:
                current_val = result[mat_name].get(tex_type, "")
                if current_val:
                    continue
                hist_val = hist.get(tex_type, "")
                if hist_val and validate_file_path(hist_val):
                    result[mat_name][tex_type] = hist_val
        else:
            if mat_name not in history_by_mat:
                continue
            hist = history_by_mat[mat_name]
            merged = {}
            for tex_type in _TEX_ATTRS:
                val = hist.get(tex_type, "")
                if val and validate_file_path(val):
                    merged[tex_type] = val
                else:
                    merged[tex_type] = ""
            result[mat_name] = merged

    return result


# [Auto Texture] Delete nodetmp persistence file on cleanup - Modified: 2026-06-09
def cleanup_nodetmp():
    tmp_path = ""
    if bpy.data.filepath:
        tmp_path = os.path.join(os.path.dirname(bpy.data.filepath), _NODETMP_FILENAME)

    if not tmp_path or not os.path.exists(tmp_path):
        return

    try:
        os.remove(tmp_path)
    except OSError as e:
        log.warning(f"Failed to delete nodetmp.txt: {e}")


def register():
    pass


def unregister():
    pass
