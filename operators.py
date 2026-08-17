"""
Blender operators and UI for Node Runner import/export.

Copyright (C) 2024 Noah Thiering
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Modifications (2026-06-09) by Auto Texture contributors:
  - Added Auto Texture matching and application operators:
    NODE_OT_match_textures, NODE_OT_apply_textures,
    NODE_OT_clear_texture_matches, NODE_OT_select_texture_directory
  - Added TextureMatchItem and NodeRunnerProperties property groups
  - Added AUTO_NODE_RUNNER_preferences with language selection (AUTO/en_US/zh_CN)
  - Removed panel class (moved to panel.py)
  - Integrated i18n module for internationalized messages
  - Integrated history module for nodetmp.txt persistence
  - Added template-based node tree application via deserialize_node_tree:
    _rewrite_template_paths, _rename_template_nodes,
    _remove_missing_links, _apply_template_to_material
  - Added fuzzy material name matching: _build_mat_name_pattern
  - Added external match_rules.json loading: _load_match_rules, _get_match_rules
  - Changed colorspace_name in ao.json/noao.json templates:
    Metallic/Roughness/Normal from sRGB to Non-Color (PBR standard)
  - Changed blend_type in ao.json from MULTIPLY to BURN for AO mixing
  - All bl_idname values use __package__ prefix for addon isolation

Modifications (2026-08-16) by Auto Texture contributors:
  - Added AI adjustment feature: NODE_OT_ai_adjust operator with background
    thread execution and bpy.app.timers for main-thread updates
  - Added path placeholder system: _build_path_placeholders,
    _to_placeholder_path, _expand_placeholder_path for AI-friendly paths
  - Added AI submission/parsing: _build_ai_submission_data,
    _parse_ai_return_data with three-tier path validation
  - Added subprocess execution: _run_ai_subprocess with UTF-8 encoding
    and PYTHONIOENCODING env var for Windows compatibility
  - Added GGUF model management: _scan_gguf_models, _resolve_gguf_directory
  - Modified match_orchestrate to return (results, classification) tuple
  - Added classified_files property to TextureMatchItem for AI unmatch data
  - Added ai_state, ai_prompt, ai_model properties to NodeRunnerProperties
  - Used bpy.data.scenes instead of bpy.context.scene in timer callbacks
    to avoid "Python context internal state bug" errors
"""

import logging

import bpy

_pkg = __package__.rpartition(".")[2] if "." in __package__ else __package__

from .constants import EXPORT_HEADER
from .encoding import (
    FORMAT_HASH,
    FORMAT_JSON,
    FORMAT_AI_JSON,
    FORMAT_XML,
    encode_as,
    decode_as,
    detect_format,
)
from .serialize import serialize_node_tree, dump_node_tree
from .deserialize import deserialize_node_tree

log = logging.getLogger(__name__)

# Blender EnumProperty items for format selection.
_FORMAT_ITEMS = [
    (FORMAT_HASH, "Hash (Base64)", "Compressed base64-encoded string (default)"),
    (FORMAT_JSON, "JSON", "Human-readable JSON (verbose)"),
    (
        FORMAT_AI_JSON,
        "AI JSON",
        "Compact readable JSON – ideal for AI / chat sharing",
    ),
    (FORMAT_XML, "XML", "Human-readable XML"),
]


def _blender_version_string():
    """Return the current Blender version as ``'X.Y.Z'``."""
    v = bpy.app.version
    return f"{v[0]}.{v[1]}.{v[2]}"


_SUPPORTED_TREE_TYPES = {"ShaderNodeTree", "GeometryNodeTree"}

# Object types that can carry a Geometry Nodes modifier. Used when the
# user invokes Import on an empty GN editor so we know whether we can
# auto-attach a new modifier.
_GN_OBJECT_TYPES = frozenset(
    {"MESH", "CURVE", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}
)


def _supported_editor_poll(context):
    """True when the active editor is a Shader or Geometry Nodes editor.

    Does NOT require an existing tree — Import will auto-create one
    when the editor is empty.
    """
    space = getattr(context, "space_data", None)
    if space is None:
        return False
    if getattr(space, "type", None) != "NODE_EDITOR":
        return False
    return getattr(space, "tree_type", None) in _SUPPORTED_TREE_TYPES


def _supported_tree_poll(context):
    """True when the active editor has a live Shader or GN tree.

    Stricter than ``_supported_editor_poll`` — used by Export, which
    cannot run without an existing tree to read nodes from.
    """
    if not _supported_editor_poll(context):
        return False
    return getattr(context.space_data, "edit_tree", None) is not None


def _find_node_editor_tree(context, tree_type):
    """Walk every open area and return the first node-editor edit_tree
    matching *tree_type*. Used when the operator was invoked from a
    space (file browser, etc.) that has no edit_tree of its own.
    """
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != "NODE_EDITOR":
            continue
        for space in area.spaces:
            if space.type != "NODE_EDITOR":
                continue
            tree = getattr(space, "edit_tree", None)
            if tree is not None and tree.bl_idname == tree_type:
                return tree
    return None


def _ensure_default_tree(operator, context, payload_tree_type):
    """Create a default tree of *payload_tree_type* and attach it to the
    active object so Import has somewhere to deserialize into.

    Returns the new ``NodeTree``, or ``None`` if attachment isn't
    possible (no active object, wrong object type for GN, etc.).
    """
    obj = getattr(context, "active_object", None)
    if obj is None:
        operator.report(
            {"ERROR"},
            "No active object to attach a new tree to. "
            "Select an object and try again.",
        )
        return None

    tree_name = "Imported Nodes"

    if payload_tree_type == "GeometryNodeTree":
        if obj.type not in _GN_OBJECT_TYPES:
            operator.report(
                {"ERROR"},
                f"Cannot add Geometry Nodes to a {obj.type.title()} object",
            )
            return None
        ng = bpy.data.node_groups.new(tree_name, "GeometryNodeTree")
        # A GN modifier requires the tree to have at least a Group
        # Output node. Seed a Geometry passthrough so the modifier
        # evaluates without errors before the user wires their imported
        # nodes in.
        ng.interface.new_socket(
            "Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
        )
        ng.interface.new_socket(
            "Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
        )
        gi = ng.nodes.new("NodeGroupInput")
        gi.location = (-200, 0)
        go = ng.nodes.new("NodeGroupOutput")
        go.location = (200, 0)
        ng.links.new(gi.outputs[0], go.inputs[0])
        mod = obj.modifiers.new(name="GeometryNodes", type="NODES")
        mod.node_group = ng
        operator.report(
            {"INFO"},
            f"Created new Geometry Nodes modifier on '{obj.name}'",
        )
        return ng

    if payload_tree_type == "ShaderNodeTree":
        if not hasattr(obj.data, "materials"):
            operator.report(
                {"ERROR"},
                f"Object '{obj.name}' cannot hold materials",
            )
            return None
        mat = bpy.data.materials.new(tree_name)
        mat.use_nodes = True
        # Drop the auto-added Principled BSDF but keep the Material
        # Output — the shader needs an output node to render.
        for n in list(mat.node_tree.nodes):
            if n.bl_idname != "ShaderNodeOutputMaterial":
                mat.node_tree.nodes.remove(n)
        obj.data.materials.append(mat)
        for i, slot in enumerate(obj.material_slots):
            if slot.material is mat:
                obj.active_material_index = i
                break
        operator.report(
            {"INFO"},
            f"Created new material on '{obj.name}'",
        )
        return mat.node_tree

    return None


# Addon preferences


class AUTO_NODE_RUNNER_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    import_at_cursor: bpy.props.BoolProperty(
        name="Import at Cursor",
        description="Offset imported nodes to the mouse cursor position",
        default=True,
    )  # type: ignore

    select_imported: bpy.props.BoolProperty(
        name="Select Imported Nodes",
        description="Select only the imported nodes after import",
        default=True,
    )  # type: ignore

    language: bpy.props.EnumProperty(
        name="Language",
        description="Interface language (AUTO follows Blender system language)",
        items=[
            ("AUTO", "Auto", "Follow Blender system language"),
            ("en_US", "English", "English"),
            ("zh_CN", "中文", "Chinese"),
        ],
        default="AUTO",
    )  # type: ignore

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_at_cursor")
        layout.prop(self, "select_imported")
        layout.prop(self, "language")
        # [Auto Texture] Uninstall button - removes nodetmp.txt and cleans up - Modified: 2026-06-09
        layout.separator()
        layout.operator(_pkg + ".uninstall_addon", icon="TRASH")
        # [Auto Texture] Uninstall button - removes nodetmp.txt and cleans up - Modified: 2026-06-09
        layout.separator()
        layout.operator(_pkg + ".uninstall_addon", icon="TRASH")


def _get_prefs(context):
    """Return addon preferences, with safe fallback defaults."""
    prefs = context.preferences.addons.get(__package__)
    if prefs:
        return prefs.preferences
    return None


# [Auto Texture] Uninstall operator - cleans data then removes the addon - Modified: 2026-06-09
class NODE_OT_uninstall_addon(bpy.types.Operator):
    """Clean up persistent data and uninstall this addon"""

    bl_idname = _pkg + ".uninstall_addon"
    bl_label = "Uninstall"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from . import history
        history.cleanup_nodetmp()
        addon_pkg = __package__
        if addon_pkg in context.preferences.addons:
            try:
                bpy.ops.preferences.addon_disable(module=addon_pkg)
            except Exception:
                pass
            try:
                bpy.ops.preferences.addon_remove(module=addon_pkg)
            except Exception:
                pass
        return {"FINISHED"}


# [Auto Texture] Set texture directory operator (safe way to set from panel) - Modified: 2026-06-09
class NODE_OT_set_texture_directory(bpy.types.Operator):
    """Set the resource directory for texture matching"""

    bl_idname = _pkg + ".set_texture_directory"
    bl_label = "Set Directory"
    bl_options = {"REGISTER", "UNDO"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    def execute(self, context):
        scene = context.scene
        node_runner = scene.node_runner
        if self.directory and os.path.isdir(self.directory):
            node_runner.texture_directory = self.directory
        return {"FINISHED"}


# Shared helpers


def _strip_image_paths(data):
    """Remove ``filepath`` keys from every image dict inside *data*."""
    nodes = data.get("nodes", {})
    for node_data in nodes.values():
        if isinstance(node_data, dict):
            img = node_data.get("image")
            if isinstance(img, dict):
                img.pop("filepath", None)


def _build_export_string(data, export_name, fmt, include_image_paths=True):
    """Encode *data* in the requested format and return the final string.

    For hash format the traditional ``Name__NR<base64>`` form is used.
    For JSON / XML the export name is embedded in the data so that the
    output is a valid, self-contained document.

    The Blender version is always embedded in the data so the importer
    can warn when versions differ.
    """
    data = dict(data)
    data["blender_version"] = _blender_version_string()

    if not include_image_paths:
        _strip_image_paths(data)

    if fmt in (FORMAT_JSON, FORMAT_AI_JSON, FORMAT_XML):
        data["export_name"] = export_name
        return encode_as(data, fmt)

    # Hash – prefix with name + header
    encoded = encode_as(data, fmt)
    return (export_name or "MyNodes") + EXPORT_HEADER + encoded


def _strip_header_and_detect(raw):
    """Strip any Node Runner header and detect the data format.

    Returns ``(format, payload)`` where *payload* is the raw data string
    ready to be decoded.
    """
    # Hash header – only used for the base64 format
    if EXPORT_HEADER in raw:
        payload = raw.split(EXPORT_HEADER, 1)[1]
        return FORMAT_HASH, payload

    # JSON / XML are valid documents – auto-detect from content
    fmt = detect_format(raw)
    return fmt, raw


def _do_import(operator, context, raw, mouse_x=None, mouse_y=None):
    """Shared import logic for the clipboard and file Import operators.

    Decodes *raw*, checks the embedded Blender version, and either
    proceeds directly or pops a confirmation dialog when versions differ.
    Auto-creates a default node tree if the active editor is empty.
    """
    fmt, payload = _strip_header_and_detect(raw)

    try:
        data = decode_as(payload, fmt)
    except ValueError as exc:
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}

    # When invoked from a file picker, context.space_data is the file
    # browser, which has no edit_tree. Fall back to scanning open areas
    # for a node editor showing a tree of the right type.
    edit_tree = getattr(context.space_data, "edit_tree", None)
    payload_tree_type = data.get("tree_type", "ShaderNodeTree")
    if edit_tree is None or edit_tree.bl_idname != payload_tree_type:
        edit_tree = _find_node_editor_tree(context, payload_tree_type) or edit_tree

    auto_created = False
    if edit_tree is None:
        edit_tree = _ensure_default_tree(operator, context, payload_tree_type)
        if edit_tree is None:
            return {"CANCELLED"}
        auto_created = True

    # Check Blender version
    export_version = data.get("blender_version", "")
    current_version = _blender_version_string()

    if export_version and export_version != current_version:
        # Stash decoded data for the confirm operator. The confirm
        # operator re-reads ``space_data.edit_tree`` after the user
        # accepts; by then any modifier/material we just added will be
        # picked up by the editor.
        bpy.types.WindowManager.nr_pending_data = data
        bpy.types.WindowManager.nr_pending_mouse = (mouse_x, mouse_y)
        bpy.types.WindowManager.nr_pending_auto_created = auto_created
        return bpy.ops.node_runner.confirm_import(
            "INVOKE_DEFAULT",
            export_version=export_version,
            current_version=current_version,
        )

    return _apply_import(
        operator, context, data, mouse_x, mouse_y,
        edit_tree=edit_tree, auto_created=auto_created,
    )


def _apply_import(
    operator, context, data, mouse_x=None, mouse_y=None,
    edit_tree=None, auto_created=False,
):
    """Deserialize decoded *data* into the active node tree.

    This is the second half of import, called after any version-mismatch
    confirmation has been accepted (or skipped). If *edit_tree* is
    provided, it overrides ``space_data.edit_tree`` — used when the
    caller just auto-created a tree that the editor hasn't picked up
    yet within the same operator invocation.
    """
    if edit_tree is None:
        edit_tree = context.space_data.edit_tree
    if edit_tree is None:
        operator.report({"WARNING"}, "No active node tree to import into")
        return {"CANCELLED"}

    # Reject payloads whose source tree type does not match the active editor.
    # Legacy exports (no tree_type field) are assumed to be shader and are
    # only allowed into a ShaderNodeTree.
    payload_tree_type = data.get("tree_type", "ShaderNodeTree")
    target_tree_type = edit_tree.bl_idname
    if payload_tree_type != target_tree_type:
        operator.report(
            {"ERROR"},
            f"Cannot import {payload_tree_type} data into {target_tree_type}",
        )
        return {"CANCELLED"}

    # Pop metadata that isn't part of the node-tree payload
    data.pop("export_name", None)
    data.pop("blender_version", None)

    # When we auto-created the tree, the seeded Group Input/Output and
    # passthrough Geometry interface socket exist only so the modifier
    # could bind to a valid tree. If the payload brings its own Group
    # I/O, clear the seeds so they don't end up as duplicates. If the
    # payload only contains body nodes (a partial export), keep the
    # seeds — otherwise the tree would have no Group Output and the
    # modifier would error.
    if auto_created:
        node_types = {
            nd.get("type") for nd in data.get("nodes", {}).values()
        }
        payload_has_output = "NodeGroupOutput" in node_types
        payload_has_input = "NodeGroupInput" in node_types
        if payload_has_output:
            for node in list(edit_tree.nodes):
                if node.bl_idname == "NodeGroupOutput":
                    edit_tree.nodes.remove(node)
        if payload_has_input:
            for node in list(edit_tree.nodes):
                if node.bl_idname == "NodeGroupInput":
                    edit_tree.nodes.remove(node)
        # Strip seeded interface sockets that the payload's Group I/O
        # will recreate. Only the matched directions are wiped.
        if hasattr(edit_tree, "interface") and (
            payload_has_input or payload_has_output
        ):
            for item in list(edit_tree.interface.items_tree):
                if item.item_type != "SOCKET":
                    continue
                if item.in_out == "INPUT" and payload_has_input:
                    edit_tree.interface.remove(item)
                elif item.in_out == "OUTPUT" and payload_has_output:
                    edit_tree.interface.remove(item)

    # Deselect all existing nodes
    for node in edit_tree.nodes:
        node.select = False

    # Track which nodes already exist
    existing_names = set(n.name for n in edit_tree.nodes)

    socket_id_map = {}
    deserialize_node_tree(edit_tree, data, socket_id_map)

    # Find freshly created nodes
    new_nodes = [n for n in edit_tree.nodes if n.name not in existing_names]

    # Select imported nodes
    prefs = _get_prefs(context)
    select_imported = prefs.select_imported if prefs else True
    if select_imported:
        for node in new_nodes:
            node.select = True
        if new_nodes:
            edit_tree.nodes.active = new_nodes[0]

    # Offset to mouse cursor position
    if mouse_x is not None and mouse_y is not None and new_nodes:
        try:
            region = context.region
            mouse_view = region.view2d.region_to_view(mouse_x, mouse_y)
            min_x = min(n.location.x for n in new_nodes)
            max_x = max(n.location.x + n.dimensions.x for n in new_nodes)
            min_y = min(n.location.y - n.dimensions.y for n in new_nodes)
            max_y = max(n.location.y for n in new_nodes)
            center_x = (min_x + max_x) / 2
            center_y = (min_y + max_y) / 2

            offset_x = mouse_view[0] - center_x
            offset_y = mouse_view[1] - center_y

            for node in new_nodes:
                if node.parent is None:
                    node.location.x += offset_x
                    node.location.y += offset_y
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass

    # When we auto-created a Geometry Nodes modifier, its per-instance
    # input values were initialized before the imported tree's interface
    # sockets existed. Rebinding pushes the freshly deserialized
    # interface defaults into the modifier so a fresh import renders the
    # same result the source tree did, without the user having to type
    # in every value by hand. Then overlay any modifier_values captured
    # from the source binding so the exact look (Leaf Density = 8 etc.)
    # is reproduced — those override the interface defaults.
    if auto_created and edit_tree.bl_idname == "GeometryNodeTree":
        obj = getattr(context, "active_object", None)
        if obj is not None:
            for mod in obj.modifiers:
                if mod.type == "NODES" and mod.node_group is edit_tree:
                    mod.node_group = None
                    mod.node_group = edit_tree
                    _apply_modifier_values(
                        mod, data.get("modifier_values"), socket_id_map
                    )
                    break

    node_count = len(new_nodes)
    operator.report(
        {"INFO"}, f"Imported {node_count} node{'s' if node_count != 1 else ''}"
    )
    return {"FINISHED"}


# Export operator


def _format_extension(fmt):
    """Return the conventional file extension (with dot) for *fmt*."""
    if fmt in (FORMAT_JSON, FORMAT_AI_JSON):
        return ".json"
    if fmt == FORMAT_XML:
        return ".xml"
    return ".txt"


def _find_modifier_for_tree(context, edit_tree):
    """Return a Geometry Nodes modifier whose node_group is *edit_tree*.

    Prefers the active object's modifier so users get values from the
    binding they are looking at; falls back to the first modifier in the
    scene that uses the tree. Returns ``None`` if no modifier is bound
    or the tree isn't a Geometry Nodes tree.
    """
    if edit_tree.bl_idname != "GeometryNodeTree":
        return None
    active = getattr(context, "active_object", None)
    if active is not None:
        for mod in active.modifiers:
            if mod.type == "NODES" and mod.node_group is edit_tree:
                return mod
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == "NODES" and mod.node_group is edit_tree:
                return mod
    return None


def _serialize_modifier_value(value):
    """Convert a modifier socket value to a JSON-friendly representation.

    ID references (Collection, Object, Material, ...) become a small dict
    ``{"__id__": <type>, "name": <name>}`` so the importer can attempt
    to resolve them by name in the target file.
    """
    if value is None:
        return None
    if isinstance(value, bpy.types.ID):
        return {"__id__": type(value).__name__, "name": value.name}
    if hasattr(value, "__len__") and not isinstance(value, str):
        try:
            return [float(x) for x in value]
        except (TypeError, ValueError):
            return list(value)
    return value


def _collect_modifier_values(mod):
    """Capture per-instance modifier values keyed by socket identifier.

    Skips the ``_use_attribute`` / ``_attribute_name`` companion keys —
    those are toggle metadata, not the user-facing values.
    """
    out = {}
    for key in mod.keys():
        if key.endswith("_use_attribute") or key.endswith("_attribute_name"):
            continue
        out[key] = _serialize_modifier_value(mod[key])
    return out


def _apply_modifier_values(mod, values, socket_id_map):
    """Restore per-instance modifier values captured at export time.

    Identifiers are remapped through *socket_id_map* because creating
    interface sockets during deserialize allocates fresh IDs. ID
    references (collections, objects, materials) are resolved by name;
    if the target file doesn't have that data block, the slot is left
    unset rather than crashing the import.
    """
    if not values:
        return
    for old_id, raw_value in values.items():
        new_id = socket_id_map.get(old_id, old_id)
        try:
            value = _resolve_id_value(raw_value)
        except (TypeError, KeyError):
            continue
        if value is None and isinstance(raw_value, dict) and "__id__" in raw_value:
            # ID reference that doesn't exist in this file — skip
            continue
        try:
            mod[new_id] = value
        except (TypeError, KeyError, AttributeError):
            log.debug("Could not set modifier value '%s'", new_id)


def _resolve_id_value(payload):
    """Resolve a serialized ID dict back to a Blender ID block by name.

    Returns ``None`` if no matching ID exists in the current file.
    """
    if not isinstance(payload, dict) or "__id__" not in payload:
        return payload
    type_to_data = {
        "Collection": bpy.data.collections,
        "Object": bpy.data.objects,
        "Material": bpy.data.materials,
        "Image": bpy.data.images,
        "Texture": bpy.data.textures,
        "World": bpy.data.worlds,
    }
    data_block = type_to_data.get(payload["__id__"])
    if data_block is None:
        return None
    return data_block.get(payload["name"])


def _build_export_payload(operator, context):
    """Serialize the selected nodes for *operator*.

    Returns ``(export_str, fmt_label)`` on success or ``(None, error_msg)``.
    """
    edit_tree = context.space_data.edit_tree
    if edit_tree is None:
        return None, "No active node tree"

    selected = context.selected_nodes or []
    names = [n.name for n in selected]
    if not names:
        return None, "No exportable nodes selected"

    data = serialize_node_tree(edit_tree, selected_node_names=names)

    # Print all node properties to stdout for debugging
    dump_node_tree(edit_tree, selected_only=True)

    # Capture modifier values so re-imports recreate the same look the
    # source object had, not just the tree's interface defaults.
    mod = _find_modifier_for_tree(context, edit_tree)
    if mod is not None:
        data["modifier_values"] = _collect_modifier_values(mod)

    export_str = _build_export_string(
        data,
        operator.export_name or "MyNodes",
        operator.export_format,
        include_image_paths=operator.include_image_paths,
    )
    fmt_label = {k: v for k, v, _ in _FORMAT_ITEMS}.get(
        operator.export_format, operator.export_format
    )
    return export_str, fmt_label


class NODE_RUNNER_OT_export_clipboard(bpy.types.Operator):
    """Copy selected nodes to the clipboard as a Node Runner string"""

    bl_idname = _pkg + ".export_clipboard"
    bl_label = "Copy to Clipboard"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _supported_tree_poll(context)

    export_name: bpy.props.StringProperty(
        name="Name",
        default="MyNodes",
        description="Label for the exported data",
    )  # type: ignore
    export_format: bpy.props.EnumProperty(
        name="Format",
        items=_FORMAT_ITEMS,
        default=FORMAT_HASH,
        description="Output format for the exported data",
    )  # type: ignore
    include_image_paths: bpy.props.BoolProperty(
        name="Include Image Paths",
        default=True,
        description=(
            "Store absolute file paths for image textures so they "
            "can be loaded automatically on import"
        ),
    )  # type: ignore

    def invoke(self, context, event):
        # Use a standard property dialog. The OK button is renamed to
        # "Copy to Clipboard" so there is no ambiguity about what
        # confirming the dialog does.
        return context.window_manager.invoke_props_dialog(
            self, width=320, confirm_text="Copy to Clipboard"
        )

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(self, "export_name", text="Name")
        col.prop(self, "export_format", text="Format")
        col.prop(self, "include_image_paths")

    def execute(self, context):
        export_str, fmt_or_err = _build_export_payload(self, context)
        if export_str is None:
            self.report({"WARNING"}, fmt_or_err)
            return {"CANCELLED"}
        context.window_manager.clipboard = export_str
        self.report(
            {"INFO"},
            f"Exported '{self.export_name}' as {fmt_or_err} - copied to clipboard",
        )
        return {"FINISHED"}


class NODE_RUNNER_OT_export_file(bpy.types.Operator):
    """Save selected nodes to a file as a Node Runner string"""

    bl_idname = _pkg + ".export_file"
    bl_label = "Save to File"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _supported_tree_poll(context)

    # File-browser fields
    filepath: bpy.props.StringProperty(
        subtype="FILE_PATH", options={"SKIP_SAVE"}
    )  # type: ignore
    filter_glob: bpy.props.StringProperty(
        default="*.txt;*.json;*.xml;*.nr", options={"HIDDEN", "SKIP_SAVE"}
    )  # type: ignore

    # Operator options shown in the file browser sidebar.
    export_name: bpy.props.StringProperty(
        name="Name",
        default="MyNodes",
        description="Label for the exported data",
    )  # type: ignore
    export_format: bpy.props.EnumProperty(
        name="Format",
        items=_FORMAT_ITEMS,
        default=FORMAT_HASH,
        description="Output format for the exported data",
    )  # type: ignore
    include_image_paths: bpy.props.BoolProperty(
        name="Include Image Paths",
        default=True,
        description=(
            "Store absolute file paths for image textures so they "
            "can be loaded automatically on import"
        ),
    )  # type: ignore

    def invoke(self, context, event):
        if not self.filepath:
            ext = _format_extension(self.export_format)
            base = (self.export_name or "MyNodes").strip() or "MyNodes"
            self.filepath = base + ext
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath:
            self.report({"ERROR"}, "No file path provided")
            return {"CANCELLED"}
        # If the user typed an extension that disagrees with the
        # selected format, infer the format from the extension so the
        # file content matches what the OS expects.
        lower = self.filepath.lower()
        if lower.endswith(".json") and self.export_format not in (FORMAT_JSON, FORMAT_AI_JSON):
            self.export_format = FORMAT_JSON
        elif lower.endswith(".xml") and self.export_format != FORMAT_XML:
            self.export_format = FORMAT_XML

        export_str, fmt_or_err = _build_export_payload(self, context)
        if export_str is None:
            self.report({"WARNING"}, fmt_or_err)
            return {"CANCELLED"}
        try:
            with open(self.filepath, "w", encoding="utf-8") as fp:
                fp.write(export_str)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not write file: {exc}")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Exported '{self.export_name}' as {fmt_or_err} to {self.filepath}",
        )
        return {"FINISHED"}


# Import operators


class NODE_RUNNER_OT_import_clipboard(bpy.types.Operator):
    """Import nodes from the clipboard"""

    bl_idname = _pkg + ".import_clipboard"
    bl_label = "From Clipboard"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _supported_editor_poll(context)

    import_at_cursor: bpy.props.BoolProperty(
        name="Import at Cursor",
        description="Offset imported nodes to the mouse cursor position",
        default=True,
    )  # type: ignore

    def invoke(self, context, event):
        self._mouse_x = event.mouse_region_x
        self._mouse_y = event.mouse_region_y
        prefs = _get_prefs(context)
        if prefs:
            self.import_at_cursor = prefs.import_at_cursor
        return self.execute(context)

    def execute(self, context):
        raw = context.window_manager.clipboard
        if not raw:
            self.report({"WARNING"}, "Clipboard is empty")
            return {"CANCELLED"}
        if self.import_at_cursor:
            mouse_x = getattr(self, "_mouse_x", None)
            mouse_y = getattr(self, "_mouse_y", None)
        else:
            mouse_x = mouse_y = None
        return _do_import(self, context, raw, mouse_x, mouse_y)


class NODE_RUNNER_OT_import_file(bpy.types.Operator):
    """Import nodes from a file"""

    bl_idname = _pkg + ".import_file"
    bl_label = "Open File"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _supported_editor_poll(context)

    filepath: bpy.props.StringProperty(
        subtype="FILE_PATH", options={"SKIP_SAVE"}
    )  # type: ignore
    filter_glob: bpy.props.StringProperty(
        default="*.txt;*.json;*.xml;*.nr", options={"HIDDEN", "SKIP_SAVE"}
    )  # type: ignore

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath:
            self.report({"ERROR"}, "No file path provided")
            return {"CANCELLED"}
        try:
            with open(self.filepath, "r", encoding="utf-8") as fp:
                raw = fp.read()
        except OSError as exc:
            self.report({"ERROR"}, f"Could not read file: {exc}")
            return {"CANCELLED"}
        if not raw.strip():
            self.report({"WARNING"}, "File is empty")
            return {"CANCELLED"}
        # File-picker import has no spatial mouse context — drop nodes
        # at the tree's existing center rather than at a stale cursor.
        return _do_import(self, context, raw, None, None)


# Version-mismatch confirmation


class NODE_RUNNER_OT_confirm_import(bpy.types.Operator):
    """Confirm import when the Blender version differs"""

    bl_idname = _pkg + ".confirm_import"
    bl_label = "Version Mismatch"
    bl_options = {"INTERNAL"}

    export_version: bpy.props.StringProperty()  # type: ignore
    current_version: bpy.props.StringProperty()  # type: ignore

    def draw(self, context):
        layout = self.layout
        layout.label(text="Blender version mismatch!", icon="ERROR")
        layout.label(text=f"Exported with: {self.export_version}")
        layout.label(text=f"Current:       {self.current_version}")
        layout.separator()
        layout.label(text="Node data may not import correctly.")
        layout.label(text="Continue anyway?")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        # Retrieve stashed data from _do_import
        data = getattr(bpy.types.WindowManager, "nr_pending_data", None)
        mouse = getattr(bpy.types.WindowManager, "nr_pending_mouse", (None, None))
        auto_created = getattr(
            bpy.types.WindowManager, "nr_pending_auto_created", False
        )

        if data is None:
            self.report({"ERROR"}, "No pending import data")
            return {"CANCELLED"}

        # Clean up
        del bpy.types.WindowManager.nr_pending_data
        del bpy.types.WindowManager.nr_pending_mouse
        if hasattr(bpy.types.WindowManager, "nr_pending_auto_created"):
            del bpy.types.WindowManager.nr_pending_auto_created

        return _apply_import(
            self, context, data, mouse[0], mouse[1], auto_created=auto_created,
        )


# Context menu (submenu)


class NODE_RUNNER_MT_menu(bpy.types.Menu):
    """Node Runner submenu"""

    bl_idname = _pkg + "_MT_menu"
    bl_label = "Node Runner"

    def draw(self, context):
        layout = self.layout

        layout.label(text="Export", icon="EXPORT")
        layout.operator(
            NODE_RUNNER_OT_export_clipboard.bl_idname,
            text="Copy to Clipboard",
            icon="COPYDOWN",
        )
        layout.operator(
            NODE_RUNNER_OT_export_file.bl_idname,
            text="Save to File...",
            icon="FILE_TICK",
        )

        layout.separator()

        layout.label(text="Import", icon="IMPORT")
        layout.operator(
            NODE_RUNNER_OT_import_clipboard.bl_idname,
            text="Paste from Clipboard",
            icon="PASTEDOWN",
        )
        layout.operator(
            NODE_RUNNER_OT_import_file.bl_idname,
            text="Open File...",
            icon="FILE_FOLDER",
        )


# Auto Texture Operators - Texture matching and application


import os
import re
import json
import copy
import locale
import subprocess
import threading
from dataclasses import dataclass, field

from . import i18n
from . import history


def _get_unique_materials_from_selection(context):
    """Get unique materials from selected objects."""
    materials = []
    seen = set()
    for obj in context.selected_objects:
        if obj.type == "MESH":
            for slot in obj.material_slots:
                if slot.material and slot.material.name not in seen:
                    seen.add(slot.material.name)
                    materials.append(slot.material)
    return materials


# DEPRECATED: 由 match_orchestrate 新流程替代
# [Auto Texture] Convert material name to fuzzy match pattern (symbols -> .?) - Modified: 2026-06-09
def _clean_material_name(name):
    """Convert material name to fuzzy match pattern.
    
    Symbols like _ - . @ etc. are replaced with .? (match any char or nothing),
    so M_0_6 can match M_0_6, M.0.6, M06, M-0-6, etc.
    """
    cleaned = re.sub(r"[_\-@.\s#$%^&*()]+", ".?", name).strip(".?")
    if not cleaned:
        return ""
    parts = [p for p in cleaned.split(".?") if p]
    return ".?".join(parts)


# DEPRECATED: 由 match_orchestrate 新流程替代
# [Auto Texture] Build regex pattern allowing optional symbols between alphanumeric segments - Modified: 2026-06-09
def _build_mat_name_pattern(mat_name):
    """Build a regex pattern from material name that allows optional symbols between characters.
    
    e.g. M_0_6 -> pattern that matches M_0_6, M.0.6, M06, M-0-6 etc.
    The pattern allows any non-alphanumeric character (or nothing) between
    each alphanumeric segment of the material name.
    """
    segments = re.split(r"[_\-@.\s#$%^&*()]+", mat_name)
    segments = [s for s in segments if s]
    if not segments:
        return ""
    return r"[^a-zA-Z0-9]*?".join(re.escape(s) for s in segments)


# [Auto Texture] Load regex match rules from external match_rules.json file - Modified: 2026-06-09
def _load_match_rules():
    """Load match rules from match_rules.json, with fallback defaults."""
    rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "match_rules.json")
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        log.warning(f"Failed to load match_rules.json: {e}, using defaults")
        return None


# [Auto Texture] Default match rules fallback when match_rules.json not found - Modified: 2026-06-09
_DEFAULT_MATCH_RULES = {
    "rules": {
        "basecolor": {"patterns": ["basecolor", "albedo", "diffuse", "color", "base", "bc", "col", "B"]},
        "metallic": {"patterns": ["metallic", "metal", "metalness", "mt", "metallicmap", "metalmap", "M"]},
        "roughness": {"patterns": ["roughness", "rough", "rgh", "roughmap", "roughnessmap", "R"]},
        "normal": {"patterns": ["normal", "norm", "nrm", "nor", "normalmap", "nmap", "N"]},
        "ao": {"patterns": ["ambient_occlusion", "ambientocclusion", "occlusion", "occlusionmap", "aomap", "ao", "occ", "A"]},
        "alpha": {"patterns": ["transparency", "opacity", "alpha", "a"]},
        "displacement": {"patterns": ["displacement", "height", "disp", "D"]},
        "specular": {"patterns": ["specular", "spec", "S"]},
        "emission": {"patterns": ["emission", "emissive", "emit", "glow", "E"]},
    },
    "image_extensions": [".png", ".jpg", ".jpeg", ".tga", ".exr", ".tif", ".tiff", ".bmp", ".hdr"],
}


# [Auto Texture] Compile match rules into regex patterns and raw pattern lists - Modified: 2026-08-16
def _get_match_rules():
    """Get compiled match rules, loading from file if available.

    Returns (type_patterns, image_extensions, type_patterns_raw) where
    type_patterns is compiled regex strings, type_patterns_raw is raw lists.
    """
    data = _load_match_rules()
    if data is None:
        data = _DEFAULT_MATCH_RULES

    rules = data.get("rules", {})
    ext_list = data.get("image_extensions", [".png", ".jpg", ".jpeg", ".tga", ".exr", ".tif", ".tiff"])
    image_extensions = frozenset(ext_list)

    type_patterns = {}
    type_patterns_raw = {}
    for tex_type, rule_data in rules.items():
        patterns = rule_data.get("patterns", [])
        if patterns:
            type_patterns[tex_type] = r"(" + "|".join(re.escape(p) for p in patterns) + ")"
            type_patterns_raw[tex_type] = patterns

    return type_patterns, image_extensions, type_patterns_raw


# [Auto Texture] Material match context dataclass for two-phase matching - Modified: 2026-08-16
@dataclass
class MaterialMatchContext:
    """Context for material name matching in the two-phase flow."""
    mat_name: str
    mat_segments: list = field(default_factory=list)
    mat_concat: str = ""
    classified_files: list = field(default_factory=list)
    remainder: str = ""
    special_symbols: set = field(default_factory=lambda: {"_", "-", ".", " ", "\t"})
    exclusive_owned_files: list = field(default_factory=list)
    degraded_shared_files: list = field(default_factory=list)


# [Auto Texture] Build material name context: segments + concatenation - Modified: 2026-08-16
def _build_material_context(mat_name):
    """Build MaterialMatchContext from material name.

    Splits mat_name into segments by special symbols, and concatenates
    all segments (removing symbols) into mat_concat.
    """
    segments = re.split(r"[_.\-\s]+", mat_name)
    segments = [s for s in segments if s]
    mat_concat = "".join(segments)
    ctx = MaterialMatchContext(mat_name=mat_name)
    ctx.mat_segments = segments
    ctx.mat_concat = mat_concat
    return ctx


# [Auto Texture] Single-char pattern判定 - Modified: 2026-08-16
def _is_single_char_pattern(pattern):
    """Return True if pattern is exactly one character."""
    return len(pattern) == 1


# [Auto Texture] Split filename into independent segments by special symbols - Modified: 2026-08-16
def _split_into_segments(filename):
    """Split filename (without extension) into segments by special symbols."""
    stem = os.path.splitext(filename)[0]
    segments = re.split(r"[_.\-\s]+", stem)
    return [s for s in segments if s]


# [Auto Texture] Single-char whole-word match: segment exactly equals pattern - Modified: 2026-08-16
def _match_single_char_whole_word(pattern, filename):
    """Return True if any segment of filename exactly matches pattern (case-insensitive)."""
    segments = _split_into_segments(filename)
    pat_lower = pattern.lower()
    for seg in segments:
        if seg.lower() == pat_lower:
            return True
    return False


# [Auto Texture] Dispatch pattern match: single-char -> whole word, multi-char -> re.search - Modified: 2026-08-16
def _dispatch_pattern_match(pattern, filename, compiled_regex=None):
    """Dispatch pattern matching: single-char uses whole-word, multi-char uses re.search."""
    if _is_single_char_pattern(pattern):
        if compiled_regex is not None:
            try:
                if not compiled_regex.search(filename):
                    return False
            except Exception:
                return False
        return _match_single_char_whole_word(pattern, filename)
    else:
        if compiled_regex is None:
            return False
        try:
            return bool(compiled_regex.search(filename))
        except Exception:
            return False


# [Auto Texture] Classify files by material using whole-word match - Modified: 2026-08-16
def _classify_files_by_material(materials, all_files):
    """Classify files by material name whole-word matching.

    Returns dict[mat_name, list[str]] of candidate files per material.
    A file is classified to a material if any file segment equals any
    material segment or the material concatenation (case-insensitive).
    """
    contexts = {}
    for mat in materials:
        mat_name = mat.name if hasattr(mat, "name") else mat
        ctx = _build_material_context(mat_name)
        contexts[mat_name] = ctx

    classification = {mat_name: [] for mat_name in contexts}

    for tex_path in all_files:
        filename = os.path.basename(tex_path)
        file_segments = _split_into_segments(filename)
        file_segments_lower = [s.lower() for s in file_segments]

        for mat_name, ctx in contexts.items():
            segs_lower = [s.lower() for s in ctx.mat_segments]
            concat_lower = ctx.mat_concat.lower()
            matched = False
            for fs in file_segments_lower:
                if fs in segs_lower or (concat_lower and fs == concat_lower):
                    matched = True
                    break
            if matched:
                classification[mat_name].append(tex_path)

    return classification


# [Auto Texture] Apply exclusivity: longest material names own files exclusively - Modified: 2026-08-16
def _apply_exclusivity(classification):
    """Apply exclusivity: files matched by longer material names are removed
    from shorter material candidates.

    Returns (classification, exclusive_owners) where exclusive_owners is
    dict[mat_name, list[str]] of files exclusively owned by each material.
    Materials with equal length do not exclusively own (conflict warning).
    """
    mat_names = list(classification.keys())
    mat_names_sorted = sorted(mat_names, key=lambda n: len(n), reverse=True)

    file_to_mats = {}
    for mat_name in mat_names:
        for f in classification[mat_name]:
            file_to_mats.setdefault(f, []).append(mat_name)

    exclusive_owners = {mat_name: [] for mat_name in mat_names}

    owned_files = set()
    for mat_name in mat_names_sorted:
        for f in classification[mat_name]:
            if f in owned_files:
                continue
            owners = file_to_mats.get(f, [])
            owner_lengths = [len(o) for o in owners]
            max_len = max(owner_lengths) if owner_lengths else 0
            same_len_owners = [o for o in owners if len(o) == max_len]
            if len(same_len_owners) > 1:
                log.warning(f"Exclusivity conflict: file {os.path.basename(f)} "
                            f"matched by multiple materials of same length: {same_len_owners}")
                continue
            if mat_name == same_len_owners[0]:
                exclusive_owners[mat_name].append(f)
                owned_files.add(f)

    for mat_name in mat_names:
        if not exclusive_owners[mat_name]:
            continue
        exclusive_set = set(exclusive_owners[mat_name])
        for other_mat in mat_names:
            if other_mat == mat_name:
                continue
            classification[other_mat] = [
                f for f in classification[other_mat] if f not in exclusive_set
            ]

    return classification, exclusive_owners


# [Auto Texture] Substitute material name from filename, return remainder - Modified: 2026-08-16
def _substitute_material_name(filename, ctx):
    """Remove material name segments from filename, return remainder.

    Removes all segments equal to ctx.mat_segments or ctx.mat_concat
    (case-insensitive), joins remaining segments with '_'.
    Returns empty string if all segments removed.
    """
    segments = _split_into_segments(filename)
    segs_lower = [s.lower() for s in ctx.mat_segments]
    concat_lower = ctx.mat_concat.lower()
    remaining = []
    for seg in segments:
        sl = seg.lower()
        if sl in segs_lower or (concat_lower and sl == concat_lower):
            continue
        remaining.append(seg)
    return "_".join(remaining)


# [Auto Texture] Match texture types in classified files using remainder - Modified: 2026-08-16
def _match_texture_types(remainder, candidate_files, type_patterns,
                         type_patterns_raw, image_extensions):
    """Match texture types using the remainder after material name substitution.

    Returns dict[str, list[str]] with candidates sorted by filename length
    descending.  Single-char patterns use whole-word match; multi-char use
    re.search.  Empty remainder returns empty lists for all types.
    """
    matches = {tex_type: [] for tex_type in type_patterns}
    if not remainder:
        return matches

    ext_pattern = r"\.(" + "|".join(re.escape(e.lstrip(".")) for e in image_extensions) + ")"

    for tex_type in type_patterns:
        raw_patterns = type_patterns_raw.get(tex_type, [])
        for raw_pattern in raw_patterns:
            full_pattern = re.escape(raw_pattern) + r"[^/\\]*?" + ext_pattern
            compiled_regex = re.compile(full_pattern, re.IGNORECASE)

            for tex_path in candidate_files:
                filename = os.path.basename(tex_path)
                if _dispatch_pattern_match(raw_pattern, filename, compiled_regex):
                    if tex_path not in matches[tex_type]:
                        matches[tex_type].append(tex_path)

        matches[tex_type].sort(key=lambda p: len(os.path.basename(p)), reverse=True)

    return matches


# [Auto Texture] Degraded segment match: whole-word for <4, fuzzy for >=4 - Modified: 2026-08-16
def _match_degraded_segment(segment, file_segments):
    """Match a degraded material name segment against file segments.

    If len(segment) < 4: whole-word match (any file_segment equals segment).
    If len(segment) >= 4: fuzzy match (any file_segment contains segment).
    Case-insensitive.
    """
    seg_lower = segment.lower()
    if len(segment) < 4:
        for fs in file_segments:
            if fs.lower() == seg_lower:
                return True
        return False
    else:
        for fs in file_segments:
            if seg_lower in fs.lower():
                return True
        return False


# [Auto Texture] Classify files by degraded matching (truncated material name) - Modified: 2026-08-16
def _classify_files_degraded(truncated_name, all_files):
    """Classify files using degraded matching with a truncated material name.

    Uses _match_degraded_segment for each segment of the truncated name.
    Returns list of matching file paths.
    """
    ctx = _build_material_context(truncated_name)
    if not ctx.mat_segments:
        return []

    result = []
    for tex_path in all_files:
        filename = os.path.basename(tex_path)
        file_segments = _split_into_segments(filename)
        for segment in ctx.mat_segments:
            if _match_degraded_segment(segment, file_segments):
                result.append(tex_path)
                break
    return result


# [Auto Texture] Orchestrate truncation degradation for unclassified files - Modified: 2026-08-16
def _orchestrate_truncate(mat_name, all_files, type_patterns,
                          type_patterns_raw, image_extensions):
    """Truncation degradation: cut material name at special symbols and retry.

    Returns (result, classified) where result is dict[str, str] per tex type,
    classified=True if candidate set was non-empty.
    """
    result = {tex_type: "" for tex_type in type_patterns}

    names_to_try = []
    current = mat_name
    while True:
        m = re.search(r"^(.+)[._\-@#$%^&*()\s]", current)
        if not m:
            break
        current = m.group(1)
        if current:
            names_to_try.append(current)

    classified_files = []
    for truncated_name in names_to_try:
        candidates = _classify_files_degraded(truncated_name, all_files)
        if candidates:
            classified_files = candidates
            break

    if not classified_files:
        return result, False

    ctx = _build_material_context(mat_name)
    for tex_path in classified_files:
        filename = os.path.basename(tex_path)
        remainder = _substitute_material_name(filename, ctx)
        if not remainder:
            remainder = filename
        type_matches = _match_texture_types(
            remainder, [tex_path], type_patterns, type_patterns_raw, image_extensions
        )
        for tex_type in type_patterns:
            if not result[tex_type] and type_matches[tex_type]:
                result[tex_type] = type_matches[tex_type][0]

    return result, True


# [Auto Texture] Fallback: shortest name match using single-char patterns only - Modified: 2026-08-16
def _fallback_shortest_name(candidate_files, type_patterns,
                             type_patterns_raw, image_extensions):
    """Shortest name fallback using only single-char patterns.

    Returns dict[str, str] picking shortest filename per type.
    """
    result = {tex_type: "" for tex_type in type_patterns}
    single_char_raw = {}
    for tex_type, patterns in type_patterns_raw.items():
        single_char_raw[tex_type] = [p for p in patterns if _is_single_char_pattern(p)]

    for tex_type in type_patterns:
        patterns = single_char_raw.get(tex_type, [])
        candidates = []
        for raw_pattern in patterns:
            for tex_path in candidate_files:
                filename = os.path.basename(tex_path)
                if _match_single_char_whole_word(raw_pattern, filename):
                    candidates.append(tex_path)
        if candidates:
            candidates = sorted(set(candidates), key=lambda p: len(os.path.basename(p)))
            result[tex_type] = candidates[0]

    return result


# DEPRECATED: 由 match_orchestrate 新流程替代
# [Auto Texture] Full match: material name pattern + type keyword, longest name first - Modified: 2026-08-16
def _match_full(mat_name, texture_files, type_patterns, image_extensions, type_patterns_raw):
    """Full match using material name pattern + type keyword.

    Returns dict[str, list[str]] with candidates sorted by filename length
    descending (longest name first).  Single-char patterns use whole-word
    matching; multi-char patterns use re.search fuzzy matching.
    """
    mat_pattern = _build_mat_name_pattern(mat_name)
    matches = {tex_type: [] for tex_type in type_patterns}
    ext_pattern = r"\.(" + "|".join(re.escape(e.lstrip(".")) for e in image_extensions) + ")"

    for tex_type in type_patterns:
        raw_patterns = type_patterns_raw.get(tex_type, [])
        for raw_pattern in raw_patterns:
            if mat_pattern:
                full_pattern = mat_pattern + r"[^/\\]*?" + re.escape(raw_pattern) + r"[^/\\]*?" + ext_pattern
            else:
                full_pattern = re.escape(raw_pattern) + r"[^/\\]*?" + ext_pattern

            compiled_regex = re.compile(full_pattern, re.IGNORECASE)

            for tex_path in texture_files:
                filename = os.path.basename(tex_path)
                if _dispatch_pattern_match(raw_pattern, filename, compiled_regex):
                    if tex_path not in matches[tex_type]:
                        matches[tex_type].append(tex_path)

        matches[tex_type].sort(key=lambda p: len(os.path.basename(p)), reverse=True)

    return matches


# DEPRECATED: 由 match_orchestrate 新流程替代
# [Auto Texture] Truncation degradation: cut material name at special symbols and retry - Modified: 2026-08-16
def _match_truncate(mat_name, texture_files, type_patterns, image_extensions, type_patterns_raw):
    """Truncation degradation match.

    Truncates material name at special symbols (e.g. xxx.001 -> xxx) and
    retries full match.  Returns dict[str, str] with matched path per type.
    """
    result = {tex_type: "" for tex_type in type_patterns}

    names_to_try = []
    current = mat_name
    while True:
        m = re.search(r"^(.+)[._\-@#$%^&*()\s]", current)
        if not m:
            break
        current = m.group(1)
        if current:
            names_to_try.append(current)

    for truncated_name in names_to_try:
        full_matches = _match_full(truncated_name, texture_files, type_patterns, image_extensions, type_patterns_raw)
        for tex_type in type_patterns:
            if not result[tex_type] and full_matches[tex_type]:
                result[tex_type] = full_matches[tex_type][0]

    return result


# DEPRECATED: 由 match_orchestrate 新流程替代
# [Auto Texture] Shortest name fallback: pure type keyword match, pick shortest filename - Modified: 2026-08-16
def _match_shortest(texture_files, type_patterns, image_extensions, type_patterns_raw):
    """Shortest name fallback match (no material name pattern).

    Only single-char patterns are used in this stage to match single-char
    filenames (e.g. D.jpg -> displacement).  Multi-char patterns are excluded
    to avoid false positives on unrelated files containing type keywords.
    Returns dict[str, str] picking the shortest filename candidate per type.
    """
    result = {tex_type: "" for tex_type in type_patterns}
    single_char_raw = {}
    for tex_type, patterns in type_patterns_raw.items():
        single_char_raw[tex_type] = [p for p in patterns if _is_single_char_pattern(p)]

    full_matches = _match_full("", texture_files, type_patterns, image_extensions, single_char_raw)
    for tex_type in type_patterns:
        if full_matches[tex_type]:
            candidates = sorted(full_matches[tex_type], key=lambda p: len(os.path.basename(p)))
            result[tex_type] = candidates[0]
    return result


# [Auto Texture] Two-phase match orchestrator: classify -> texture match - Modified: 2026-08-16
def match_orchestrate(materials, all_files):
    """Two-phase match orchestrator.

    Phase 1 (File Classification): Classify files by material name whole-word
    matching, then degraded matching for unclassified files. Exclusivity is
    NOT applied: shorter-named materials keep candidates owned by longer
    names so AI adjustment receives the full candidate list per material.
    Phase 2 (Texture Matching): For each material, substitute material name
    from classified files and match texture types; fallback to shortest name.

    Args:
        materials: list of material objects (with .name) or material name strings.
        all_files: list of texture file paths.

    Returns:
        tuple (results, classification) where
        results: dict[mat_name, dict[tex_type, str]] with matched path per type per material.
        classification: dict[mat_name, list[str]] of all candidate files per material
            (including degraded-shared), used downstream for AI unmatch submission.
    """
    type_patterns, image_extensions, type_patterns_raw = _get_match_rules()
    mat_names = [mat.name if hasattr(mat, "name") else mat for mat in materials]

    contexts = {name: _build_material_context(name) for name in mat_names}

    classification = _classify_files_by_material(materials, all_files)


    mat_names_sorted = sorted(mat_names, key=lambda n: len(n), reverse=True)
    classified_files_global = set()
    for mat_name in mat_names:
        classified_files_global.update(classification[mat_name])

    unclassified_files = [f for f in all_files if f not in classified_files_global]

    for mat_name in mat_names_sorted:
        if not unclassified_files:
            break
        ctx = contexts[mat_name]
        degraded = _classify_files_degraded(mat_name, unclassified_files)
        for f in degraded:
            if f not in classification[mat_name]:
                classification[mat_name].append(f)
            ctx.degraded_shared_files.append(f)

    results = {}
    for mat_name in mat_names:
        ctx = contexts[mat_name]
        result = {tex_type: "" for tex_type in type_patterns}
        classified = classification[mat_name]

        if not classified:
            results[mat_name] = result
            continue

        for tex_path in classified:
            filename = os.path.basename(tex_path)
            remainder = _substitute_material_name(filename, ctx)
            if not remainder:
                continue
            type_matches = _match_texture_types(
                remainder, [tex_path], type_patterns,
                type_patterns_raw, image_extensions
            )
            for tex_type in type_patterns:
                if not result[tex_type] and type_matches[tex_type]:
                    result[tex_type] = type_matches[tex_type][0]

        unmatched = [t for t in type_patterns if not result[t]]
        if unmatched:
            shortest = _fallback_shortest_name(
                classified, type_patterns, type_patterns_raw, image_extensions
            )
            for tex_type in unmatched:
                if shortest.get(tex_type, ""):
                    result[tex_type] = shortest[tex_type]

        results[mat_name] = result

    return results, classification


# [Auto Texture] Match texture files for a single material (backward compat) - Modified: 2026-08-16
def _match_textures_for_material(mat_name, texture_files):
    """Match texture files to a single material.

    Returns (matches, image_extensions) for backward compatibility where
    matches is dict[str, list[str]] (single-element list or empty list).
    """
    type_patterns, image_extensions, _ = _get_match_rules()
    orchestrated, _classification = match_orchestrate([mat_name], texture_files)
    single_result = orchestrated.get(mat_name, {tex_type: "" for tex_type in type_patterns})
    matches = {tex_type: ([path] if path else []) for tex_type, path in single_result.items()}
    return matches, image_extensions


# [Auto Texture] Scan directory for image files using configurable extensions - Modified: 2026-06-09
def _scan_directory_for_textures(directory, image_extensions=None):
    """Scan a directory recursively for image files."""
    if image_extensions is None:
        _, image_extensions, _ = _get_match_rules()
    textures = []
    if os.path.isdir(directory):
        for root, _, files in os.walk(directory):
            for f in files:
                if os.path.splitext(f.lower())[1] in image_extensions:
                    textures.append(os.path.join(root, f))
    return textures


class NODE_OT_match_textures(bpy.types.Operator):
    """Scan directory and match textures to selected materials"""

    bl_idname = _pkg + ".match_textures"
    bl_label = "匹配贴图"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        node_runner = scene.node_runner

        # [Auto Texture] Reset AI state so the adjust button is re-enabled - Modified: 2026-08-16
        node_runner.ai_state = "IDLE"

        # [Auto Texture] If no directory set, try blend file directory - Modified: 2026-06-09
        tex_dir = node_runner.texture_directory
        if not tex_dir:
            tex_dir = bpy.path.abspath("//")
            if tex_dir and os.path.isdir(tex_dir):
                node_runner.texture_directory = tex_dir
            else:
                self.report({"WARNING"}, i18n.get_text("message.select_directory_first"))
                return {"CANCELLED"}

        if ".." in tex_dir.split(os.sep) or ".." in tex_dir.split("/"):
            self.report({"WARNING"}, i18n.get_text("message.invalid_directory"))
            return {"CANCELLED"}

        if not os.path.isabs(tex_dir) or not os.path.isdir(tex_dir):
            self.report({"WARNING"}, i18n.get_text("message.invalid_directory"))
            return {"CANCELLED"}

        materials = _get_unique_materials_from_selection(context)
        if not materials:
            self.report({"WARNING"}, i18n.get_text("message.select_object_first"))
            return {"CANCELLED"}

        node_runner.texture_matches.clear()

        textures = _scan_directory_for_textures(tex_dir)

        orchestrated, classification = match_orchestrate(materials, textures)

        auto_matches = {}
        for mat in materials:
            mat_result = orchestrated.get(mat.name, {})
            auto_matches[mat.name] = {
                attr: mat_result.get(tex_type, "")
                for attr, tex_type in zip(_TEX_ATTRS, _TEX_TYPES)
            }

        history_data = history.load_texture_matches(context)
        current_mat_names = {mat.name for mat in materials}
        merged = history.merge_with_history(auto_matches, history_data, current_mat_names)

        for mat_name, paths in merged.items():
            item = node_runner.texture_matches.add()
            item.material_name = mat_name
            for attr in _TEX_ATTRS:
                setattr(item, attr, paths.get(attr, ""))
            classified = classification.get(mat_name, [])
            item.classified_files = "\n".join(classified) if classified else ""

        history.save_texture_matches(context)

        mat_count = len(node_runner.texture_matches)
        self.report({"INFO"}, i18n.get_text("message.matched_count", count=mat_count))
        return {"FINISHED"}



class NODE_OT_apply_textures(bpy.types.Operator):
    """Apply matched textures to materials"""

    bl_idname = _pkg + ".apply_textures"
    bl_label = "一键应用"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        node_runner = scene.node_runner

        if not node_runner.texture_matches:
            self.report({"WARNING"}, i18n.get_text("message.no_data_to_apply"))
            return {"CANCELLED"}

        applied_count = 0
        missing_count = 0

        for item in node_runner.texture_matches:
            mat = bpy.data.materials.get(item.material_name)
            if not mat or not mat.use_nodes:
                self.report(
                    {"WARNING"},
                    i18n.get_text("message.material_deleted", mat=item.material_name),
                )
                continue

            try:
                applied, missing = apply_to_material(mat, item)
                applied_count += 1
                missing_count += missing
            except Exception as e:
                self.report(
                    {"WARNING"},
                    i18n.get_text("message.template_load_failed", name="node.json", mat=item.material_name),
                )
                log.warning(f"Failed to apply template for material '{item.material_name}': {e}")

        self.report(
            {"INFO"},
            i18n.get_text("message.applied_count", applied=applied_count, missing=missing_count),
        )
        return {"FINISHED"}


class NODE_OT_clear_texture_matches(bpy.types.Operator):
    """Clear all texture matches"""

    bl_idname = _pkg + ".clear_texture_matches"
    bl_label = "清除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        scene.node_runner.texture_matches.clear()
        return {"FINISHED"}


class NODE_OT_select_texture_directory(bpy.types.Operator):
    """Select texture directory"""

    bl_idname = _pkg + ".select_texture_directory"
    bl_label = "选择目录"
    bl_options = {"REGISTER"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH")  # type: ignore

    def execute(self, context):
        context.scene.node_runner.texture_directory = self.directory
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# [Auto Texture] Toggle collapse state for a material block in the panel - Modified: 2026-08-16
class NODE_OT_toggle_collapse(bpy.types.Operator):
    """Toggle collapse/expand state of a material block"""

    bl_idname = _pkg + ".toggle_collapse"
    bl_label = "Toggle Collapse"
    bl_options = {"REGISTER", "UNDO"}

    material_name: bpy.props.StringProperty()  # type: ignore

    def execute(self, context):
        for item in context.scene.node_runner.texture_matches:
            if item.material_name == self.material_name:
                item.is_collapsed = not item.is_collapsed
                break
        return {"FINISHED"}


# [Auto Texture] Texture attribute names and type keys for 9-slot support - Modified: 2026-08-16
_TEX_ATTRS = (
    "basecolor_path", "metallic_path", "roughness_path", "normal_path",
    "ao_path", "alpha_path", "displacement_path", "specular_path", "emission_path",
)
_TEX_TYPES = (
    "basecolor", "metallic", "roughness", "normal",
    "ao", "alpha", "displacement", "specular", "emission",
)

# [Auto Texture] Map texture types to template Image Texture node names (unified node.json) - Modified: 2026-08-16
_TEXTURE_NODE_MAP = {
    "basecolor": ("Image Texture", "Color"),
    "metallic": ("Image Texture.001", "Color"),
    "normal": ("Image Texture.002", "Color"),
    "roughness": ("Image Texture.003", "Color"),
    "ao": ("Image Texture.004", "Color"),
    "displacement": ("Image Texture.006", "Color"),
    "emission": ("Image Texture.007", "Color"),
    "specular": ("Image Texture.008", "Color"),
    "alpha": ("Image Texture.009", "Color"),
}

# [Auto Texture] Map texture types to Group Output socket identifiers (ao has no direct socket) - Modified: 2026-08-16
_GROUP_OUTPUT_SOCKET_MAP = {
    "basecolor": "Socket_0",
    "metallic": "Socket_1",
    "roughness": "Socket_2",
    "normal": "Socket_3",
    "displacement": "Socket_6",
    "emission": "Socket_7",
    "specular": "Socket_8",
    "alpha": "Socket_9",
}


# [Auto Texture] Rewrite template JSON image paths/names to matched texture file paths - Modified: 2026-08-16
def _rewrite_template_paths(template, item):
    nodes = template.get("nodes", {})
    group_node = None
    for key, nd in nodes.items():
        if nd.get("type") == "ShaderNodeGroup":
            group_node = nd
            break

    if not group_node:
        #print(f"[AutoTexture] ERROR: No ShaderNodeGroup found in template nodes: {list(nodes.keys())}")
        return set()

    sub_tree = group_node.get("node_tree", {})
    sub_nodes = sub_tree.get("nodes", {})

    mat_name = getattr(item, "material_name", "")
    safe_mat = re.sub(r"[^\w]", "_", mat_name)[:32]

    path_map = {
        tex_type: getattr(item, attr, "")
        for tex_type, attr in zip(_TEX_TYPES, _TEX_ATTRS)
    }

    #print(f"[AutoTexture] rewrite_paths: mat={mat_name}")
    #print(f"[AutoTexture] rewrite_paths: path_map={path_map}")

    missing_types = set()

    for tex_type, (node_name, socket_name) in _TEXTURE_NODE_MAP.items():
        node_data = sub_nodes.get(node_name, {})
        img_data = node_data.get("image", {})

        if not img_data:
            #print(f"[AutoTexture]   {tex_type}: SKIPPED - no image data in node")
            continue

        file_path = path_map.get(tex_type, "")
        if file_path and os.path.exists(file_path):
            img_data["filepath"] = file_path
            base_name = os.path.basename(file_path)
            name_part = os.path.splitext(base_name)[0]
            ext_part = os.path.splitext(base_name)[1]
            img_data["name"] = f"{name_part}_{safe_mat}{ext_part}"
            #print(f"[AutoTexture]   {tex_type}: SET filepath={file_path}, name={img_data['name']}")
        else:
            img_data["filepath"] = ""
            img_data["name"] = ""
            missing_types.add(tex_type)
            #print(f"[AutoTexture]   {tex_type}: MISSING - path={file_path}")

    return missing_types


# [Auto Texture] Rename template nodes with material suffix to avoid naming conflicts - Modified: 2026-06-09
def _rename_template_nodes(template, mat_name):
    suffix = "_" + re.sub(r"[^\w]", "_", mat_name)[:32]

    nodes = template.get("nodes", {})
    group_key = None
    group_node = None
    for key, nd in nodes.items():
        if nd.get("type") == "ShaderNodeGroup":
            group_key = key
            group_node = nd
            break

    if not group_node:
        return "Group"

    group_name = group_node.get("name", group_key)
    new_group_name = group_name + suffix
    group_node["name"] = new_group_name

    sub_tree = group_node.get("node_tree", {})
    sub_name = sub_tree.get("name", "")
    sub_tree["name"] = sub_name + suffix

    sub_nodes = sub_tree.get("nodes", {})
    node_name_remap = {}

    for old_name, node_data in list(sub_nodes.items()):
        new_name = old_name + suffix
        node_data["name"] = new_name
        node_name_remap[old_name] = new_name
        sub_nodes[new_name] = sub_nodes.pop(old_name)

    sub_links = sub_tree.get("links", [])
    for link in sub_links:
        fn = link.get("from_node", "")
        tn = link.get("to_node", "")
        if fn in node_name_remap:
            link["from_node"] = node_name_remap[fn]
        if tn in node_name_remap:
            link["to_node"] = node_name_remap[tn]

    top_links = template.get("links", [])
    for link in top_links:
        fn = link.get("from_node", "")
        tn = link.get("to_node", "")
        if fn == group_key or fn == group_name:
            link["from_node"] = new_group_name
        if tn == group_key or tn == group_name:
            link["to_node"] = new_group_name

    if group_key in nodes:
        nodes[new_group_name] = nodes.pop(group_key)

    #print(f"[AutoTexture] rename_nodes: mat={mat_name}, group_key={group_key} -> {new_group_name}")

    return new_group_name


# [Auto Texture] Build dynamic connection topology: no-AO direct, no-Bump direct, unmatched disconnect - Modified: 2026-08-16
def _build_dynamic_links(template, item):
    """Dynamically adjust sub-tree links based on matched textures.

    - No AO: connect Base Color Image Texture directly to Group Output Socket_0,
      bypassing the Mix node.
    - No Bump but has Normal: connect Normal Map directly to Group Output Socket_3,
      bypassing the Bump node.
    Returns set of additional missing types (e.g. ao when no AO texture).
    """
    nodes = template.get("nodes", {})
    group_node = None
    for key, nd in nodes.items():
        if nd.get("type") == "ShaderNodeGroup":
            group_node = nd
            break
    if not group_node:
        return set()

    sub_tree = group_node.get("node_tree", {})
    sub_links = sub_tree.get("links", [])

    extra_missing = set()

    ao_path = getattr(item, "ao_path", "")
    has_ao = bool(ao_path) and os.path.exists(ao_path)

    if not has_ao:
        sub_tree["links"] = [
            lk for lk in sub_links
            if not (
                (lk.get("from_node") == "Image Texture" and lk.get("to_node") == "Mix")
                or (lk.get("from_node") == "Image Texture.004" and lk.get("to_node") == "Mix")
                or (lk.get("from_node") == "Mix" and lk.get("to_node") == "Group Output")
            )
        ]
        sub_links = sub_tree["links"]
        sub_links.append({
            "from_node": "Image Texture",
            "to_node": "Group Output",
            "from_socket": "Color",
            "from_socket_type": "NodeSocketColor",
            "from_socket_identifier": "Color",
            "to_socket": "Base Color",
            "to_socket_type": "NodeSocketColor",
            "to_socket_identifier": "Socket_0",
        })
        extra_missing.add("ao")
        #print("[AutoTexture] dynamic_links: no AO, basecolor direct to Group Output")

    normal_path = getattr(item, "normal_path", "")
    has_normal = bool(normal_path) and os.path.exists(normal_path)
    has_bump = False
    if has_normal:
        normal_filename = os.path.basename(normal_path).lower()
        if "bump" in normal_filename:
            has_bump = True

    if has_normal and not has_bump:
        sub_tree["links"] = [
            lk for lk in sub_links
            if not (
                (lk.get("from_node") == "Normal Map" and lk.get("to_node") == "Bump")
                or (lk.get("from_node") == "Bump" and lk.get("to_node") == "Group Output")
                or (lk.get("from_node") == "Image Texture.005" and lk.get("to_node") == "Bump")
            )
        ]
        sub_links = sub_tree["links"]
        sub_links.append({
            "from_node": "Normal Map",
            "to_node": "Group Output",
            "from_socket": "Normal",
            "from_socket_type": "NodeSocketVector",
            "from_socket_identifier": "Normal",
            "to_socket": "Normal",
            "to_socket_type": "NodeSocketVector",
            "to_socket_identifier": "Socket_3",
        })
        #print("[AutoTexture] dynamic_links: no Bump, Normal Map direct to Group Output")

    return extra_missing


# [Auto Texture] Remove top-level Group->BSDF links for missing textures (disconnects unmatched) - Modified: 2026-08-16
def _remove_missing_links(template, missing_types, group_node_name):
    for tex_type in missing_types:
        if tex_type in _GROUP_OUTPUT_SOCKET_MAP:
            socket_id = _GROUP_OUTPUT_SOCKET_MAP[tex_type]
            template["links"] = [
                lk for lk in template.get("links", [])
                if not (
                    lk.get("from_node", "") == group_node_name
                    and lk.get("from_socket_identifier", "") == socket_id
                )
            ]


# [Auto Texture] Load unified node.json template as a deep copy - Modified: 2026-08-16
def _load_unified_template():
    """Load unified node.json template and return a deep copy."""
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "node.json")
    with open(template_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return copy.deepcopy(data)


# [Auto Texture] Patch material node tree with template (all-unmatched protection) - Modified: 2026-08-16
def _patch_node_tree(mat, item, template):
    """Patch material node tree with template. Returns (applied_count, missing_count)."""
    all_unmatched = True
    for attr in _TEX_ATTRS:
        path = getattr(item, attr, "")
        if path and os.path.exists(path):
            all_unmatched = False
            break

    if all_unmatched:
        #print(f"[AutoTexture] patch: all slots unmatched for mat={mat.name}, skipping")
        return (0, len(_TEX_ATTRS))

    node_tree = mat.node_tree

    old_image_names = set()
    for node in node_tree.nodes:
        if hasattr(node, "image") and node.image:
            old_image_names.add(node.image.name)

    for node in list(node_tree.nodes):
        node_tree.nodes.remove(node)

    for link in list(node_tree.links):
        node_tree.links.remove(link)

    for img_name in old_image_names:
        img = bpy.data.images.get(img_name)
        if img and img.users == 0:
            bpy.data.images.remove(img)

    for attr in _TEX_ATTRS:
        path = getattr(item, attr, "")
        if path and os.path.exists(path):
            base_name = os.path.basename(path)
            name_part = os.path.splitext(base_name)[0]
            ext_part = os.path.splitext(base_name)[1]
            safe_mat = re.sub(r"[^\w]", "_", mat.name)[:32]
            unique_name = f"{name_part}_{safe_mat}{ext_part}"
            existing = bpy.data.images.get(unique_name)
            if existing:
                bpy.data.images.remove(existing)

    socket_id_map = {}
    try:
        deserialize_node_tree(node_tree, template, socket_id_map)
        #print(f"[AutoTexture] patch: deserialize SUCCESS for mat={mat.name}")
        #for node in node_tree.nodes:
            #if hasattr(node, "image") and node.image:
                #print(f"[AutoTexture]   node={node.name}, image={node.image.name}, filepath={node.image.filepath}")
    except Exception as e:
        #print(f"[AutoTexture] patch: deserialize FAILED for mat={mat.name}: {e}")
        log.warning(f"Failed to deserialize template for material '{mat.name}': {e}")
        mat.use_nodes = True
        return (0, len(_TEX_ATTRS))

    applied = 0
    missing = 0
    for attr in _TEX_ATTRS:
        path = getattr(item, attr, "")
        if path and os.path.exists(path):
            applied += 1
        else:
            missing += 1
    return (applied, missing)


# [Auto Texture] Unified apply entry: load template -> rewrite -> dynamic links -> rename -> patch - Modified: 2026-08-16
def apply_to_material(mat, item):
    """Apply unified template to material. Returns (applied_count, missing_count)."""
    #print(f"[AutoTexture] apply_to_material: mat={mat.name}")

    template = _load_unified_template()

    missing_types = _rewrite_template_paths(template, item)
    #print(f"[AutoTexture] apply_to_material: missing_types={missing_types}")

    extra_missing = _build_dynamic_links(template, item)
    missing_types |= extra_missing

    group_node_name = _rename_template_nodes(template, mat.name)

    _remove_missing_links(template, missing_types, group_node_name)
    #print(f"[AutoTexture] apply_to_material: top_links after removal={len(template.get('links', []))}")

    return _patch_node_tree(mat, item, template)


class TextureMatchItem(bpy.types.PropertyGroup):
    material_name: bpy.props.StringProperty(name="Material Name")
    basecolor_path: bpy.props.StringProperty(name="Base Color Path", subtype="FILE_PATH")
    metallic_path: bpy.props.StringProperty(name="Metallic Path", subtype="FILE_PATH")
    roughness_path: bpy.props.StringProperty(name="Roughness Path", subtype="FILE_PATH")
    normal_path: bpy.props.StringProperty(name="Normal Path", subtype="FILE_PATH")
    ao_path: bpy.props.StringProperty(name="AO Path", subtype="FILE_PATH")
    alpha_path: bpy.props.StringProperty(name="Alpha Path", subtype="FILE_PATH")
    displacement_path: bpy.props.StringProperty(name="Displacement Path", subtype="FILE_PATH")
    specular_path: bpy.props.StringProperty(name="Specular Path", subtype="FILE_PATH")
    emission_path: bpy.props.StringProperty(name="Emission Path", subtype="FILE_PATH")
    is_collapsed: bpy.props.BoolProperty(name="Is Collapsed", default=False)
    classified_files: bpy.props.StringProperty(
        name="Classified Files",
        description="All candidate file paths classified to this material, newline-separated",
        default="",
    )


def _on_texture_path_update(self, context):
    #print(f"[AutoTexture] path_updated: mat={getattr(self, 'material_name', '?')}")
    history.save_texture_matches(context)


TextureMatchItem.__annotations__["basecolor_path"] = bpy.props.StringProperty(
    name="Base Color Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["metallic_path"] = bpy.props.StringProperty(
    name="Metallic Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["roughness_path"] = bpy.props.StringProperty(
    name="Roughness Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["normal_path"] = bpy.props.StringProperty(
    name="Normal Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["ao_path"] = bpy.props.StringProperty(
    name="AO Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["alpha_path"] = bpy.props.StringProperty(
    name="Alpha Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["displacement_path"] = bpy.props.StringProperty(
    name="Displacement Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["specular_path"] = bpy.props.StringProperty(
    name="Specular Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)
TextureMatchItem.__annotations__["emission_path"] = bpy.props.StringProperty(
    name="Emission Path",
    subtype="FILE_PATH",
    update=_on_texture_path_update,
)


# [Auto Texture] AI adjustment - build directory placeholder mapping - Modified: 2026-08-16
def _build_path_placeholders(paths, root_dir=None):
    """Assign each unique directory a human-readable placeholder.

    Naming rules:
      - The scan root directory -> %C%
      - Other directories -> %<parent_dir_name>% (basename of the directory's parent)
      - Name collisions get an incrementing numeric suffix (%tex%, %tex1%, %tex2% ...)

    Returns (dir_to_placeholder, placeholder_to_dir).  Directories are sorted
    so the same input always yields the same placeholder naming.
    """
    dirs = set()
    for p in paths:
        if not isinstance(p, str):
            continue
        d = os.path.dirname(p)
        if d:
            dirs.add(d)
    sorted_dirs = sorted(dirs)
    root_norm = os.path.normpath(root_dir) if root_dir else None

    dir_to_placeholder = {}
    placeholder_to_dir = {}
    used_names = set()

    for d in sorted_dirs:
        if root_norm and os.path.normpath(d) == root_norm:
            ph = "%C%"
            dir_to_placeholder[d] = ph
            placeholder_to_dir[ph] = d
            used_names.add("C")
            break

    for d in sorted_dirs:
        if d in dir_to_placeholder:
            continue
        parent = os.path.dirname(d)
        base = os.path.basename(parent) if parent else os.path.basename(d)
        if not base or base in used_names:
            i = 1
            while (f"{base}{i}" if base else f"dir{i}") in used_names:
                i += 1
            base = f"{base}{i}" if base else f"dir{i}"
        ph = f"%{base}%"
        dir_to_placeholder[d] = ph
        placeholder_to_dir[ph] = d
        used_names.add(base)

    return dir_to_placeholder, placeholder_to_dir


# [Auto Texture] AI adjustment - convert full path to %name%/basename - Modified: 2026-08-16
def _to_placeholder_path(full_path, dir_to_placeholder):
    """Convert a full path to %placeholder%/basename form."""
    if not isinstance(full_path, str) or not full_path:
        return full_path
    d = os.path.dirname(full_path)
    ph = dir_to_placeholder.get(d)
    if ph:
        return f"{ph}/{os.path.basename(full_path)}"
    return os.path.basename(full_path) if not d else full_path


# [Auto Texture] AI adjustment - expand %name%/basename back to full path - Modified: 2026-08-16
def _expand_placeholder_path(ph_path, placeholder_to_dir):
    """Convert a %placeholder%/basename path back to a full path."""
    if not isinstance(ph_path, str) or not ph_path:
        return ph_path
    for ph, d in placeholder_to_dir.items():
        if ph_path == ph:
            return d
        if ph_path.startswith(ph + "/") or ph_path.startswith(ph + "\\"):
            rest = ph_path[len(ph) + 1:]
            return os.path.join(d, rest) if rest else d
    # Fallback: if %C% is not in the map, try each directory (shortest first = root)
    if ph_path.startswith("%C%/") or ph_path.startswith("%C%\\"):
        basename = ph_path[4:]
        for d in sorted(placeholder_to_dir.values(), key=len):
            candidate = os.path.join(d, basename)
            if os.path.isfile(candidate):
                return candidate
    return ph_path


# [Auto Texture] AI adjustment - build submission data from matches - Modified: 2026-08-16
def _build_ai_submission_data(texture_matches, classified_files_map=None, root_dir=None):
    """Build AI submission data from current matches.

    Returns (submission, placeholder_map).  Paths use %name%/basename form
    where %name% is a human-readable directory placeholder (%C% for root,
    parent dir name otherwise).  AI returns the same form; _parse_ai_return_data
    expands placeholders back to full paths.
    """
    if classified_files_map is None:
        classified_files_map = {}

    all_full_paths = []
    per_material = []
    for item in texture_matches:
        mat_name = item.material_name
        matched_files = set()
        matched_pairs = []
        for attr, tex_type in zip(_TEX_ATTRS, _TEX_TYPES):
            path = getattr(item, attr, "")
            if path:
                matched_pairs.append((tex_type, path))
                matched_files.add(path)
        classified = classified_files_map.get(mat_name)
        if classified is None:
            raw = getattr(item, "classified_files", "")
            classified = [f for f in raw.split("\n") if f] if raw else []
        unmatch_full = [f for f in classified if f not in matched_files]
        per_material.append((mat_name, matched_pairs, unmatch_full))
        all_full_paths.extend(p for _, p in matched_pairs)
        all_full_paths.extend(unmatch_full)

    dir_to_placeholder, placeholder_to_dir = _build_path_placeholders(all_full_paths, root_dir)

    submission = {}
    for mat_name, matched_pairs, unmatch_full in per_material:
        mat_data = {}
        for tex_type, full in matched_pairs:
            mat_data[tex_type] = _to_placeholder_path(full, dir_to_placeholder)
        mat_data["unmatch"] = [
            _to_placeholder_path(f, dir_to_placeholder) for f in unmatch_full
        ]
        submission[mat_name] = mat_data
    return submission, placeholder_to_dir


# [Auto Texture] AI adjustment - parse AI return data - Modified: 2026-08-16
def _parse_ai_return_data(ai_output, submission_data, placeholder_map=None):
    """Parse AI return data from output text.

    Returns dict[mat_name, {tex_type: path}].  Raises ValueError on parse failure.
    Handles outer wrapper {"response": "..."} / {"error": "..."} emitted by Text.py.
    placeholder_map ({placeholder: dir}) is used to expand %pN% back to full paths.
    """
    content_text = ai_output
    try:
        outer = json.loads(ai_output)
        if isinstance(outer, dict):
            if "error" in outer:
                raise ValueError(f"AI error: {outer['error']}")
            if "response" in outer and isinstance(outer["response"], str):
                content_text = outer["response"]
    except json.JSONDecodeError:
        pass

    json_str = None
    md_match = re.search(r"```json\s*(.*?)\s*```", content_text, re.DOTALL)
    if md_match:
        json_str = md_match.group(1)
    else:
        try:
            json.loads(content_text)
            json_str = content_text
        except (json.JSONDecodeError, ValueError):
            obj_match = re.search(r"\{.*\}", content_text, re.DOTALL)
            if obj_match:
                json_str = obj_match.group(0)

    if json_str is None:
        raise ValueError("No JSON found in AI output")

    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"JSON parse failed: {e}")

    placeholder_to_dir = placeholder_map if isinstance(placeholder_map, dict) else {}

    valid_ph_paths = set()
    for mat_name, mat_data in submission_data.items():
        if not isinstance(mat_data, dict):
            continue
        for k, v in mat_data.items():
            if k == "unmatch":
                if isinstance(v, list):
                    valid_ph_paths.update(v)
            elif isinstance(v, str) and v:
                valid_ph_paths.add(v)

    ai_result = {}
    for mat_name, mat_data in parsed.items():
        if not isinstance(mat_data, dict):
            continue
        cleaned = {}
        for k, v in mat_data.items():
            if k == "unmatch":
                continue
            if isinstance(v, str) and v:
                full = _expand_placeholder_path(v, placeholder_to_dir)
                if v in valid_ph_paths:
                    cleaned[k] = full
                elif os.path.isfile(full):
                    cleaned[k] = full
                    print(f"[AI Adjustment] accepted existing path outside candidates: {mat_name}.{k}={full}")
                else:
                    print(f"[AI Adjustment] skipped non-existent AI return: {mat_name}.{k}={full}")
                    log.warning(f"AI return path does not exist, skipped: {mat_name}.{k}={full}")
        ai_result[mat_name] = cleaned

    return ai_result


# [Auto Texture] AI adjustment - resolve runtime python path - Modified: 2026-08-16
def _resolve_runtime_python():
    """Return path to runtime/python.exe or None if missing."""
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    python_path = os.path.join(addon_dir, "runtime", "python.exe")
    if os.path.isfile(python_path):
        return python_path
    return None


# [Auto Texture] AI adjustment - resolve AI script path - Modified: 2026-08-16
def _resolve_ai_script():
    """Return path to Text.py or None if missing."""
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(addon_dir, "Text.py")
    if os.path.isfile(script_path):
        return script_path
    return None


# [Auto Texture] AI adjustment - resolve GGUF directory - Modified: 2026-08-16
def _resolve_gguf_directory():
    """Return absolute path to GGUF directory."""
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(addon_dir, "GGUF")


# [Auto Texture] AI adjustment - scan GGUF model files - Modified: 2026-08-16
def _scan_gguf_models(self, context):
    """Scan GGUF directory for .gguf model files.

    Returns list of (identifier, label, description) tuples for EnumProperty.
    """
    gguf_dir = _resolve_gguf_directory()
    if not os.path.isdir(gguf_dir):
        return [("none", "无可用模型", "")]

    models = []
    try:
        for f in os.listdir(gguf_dir):
            if f.lower().endswith(".gguf"):
                identifier = os.path.splitext(f)[0]
                models.append((identifier, f, ""))
    except OSError:
        return [("none", "无可用模型", "")]

    if not models:
        return [("none", "无可用模型", "")]

    models.sort(key=lambda m: m[1])
    return models


# [Auto Texture] AI adjustment - resolve selected model absolute path - Modified: 2026-08-16
def _resolve_selected_model_path(selected_model):
    """Resolve selected model identifier to absolute file path.

    Returns None if selected_model is empty, 'none', or file doesn't exist.
    """
    if not selected_model or selected_model == "none":
        return None
    gguf_dir = _resolve_gguf_directory()
    model_path = os.path.join(gguf_dir, selected_model + ".gguf")
    if os.path.isfile(model_path):
        return model_path
    return None


# [Auto Texture] AI adjustment - tolerant subprocess output decoding - Modified: 2026-08-16
def _decode_subprocess_output(data):
    """Tolerantly decode subprocess output bytes to string.

    Tries UTF-8, GBK, locale default, then falls back to UTF-8 with replace.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "gbk", locale.getpreferredencoding(False)):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


# [Auto Texture] AI adjustment - run AI subprocess - Modified: 2026-08-16
def _run_ai_subprocess(python_path, script_path, submission_json, prompt, model_path, timeout=120):
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        process = subprocess.Popen(
            [python_path, script_path, "--data", submission_json,
             "--prompt", prompt, "--model", model_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except Exception as e:
        return (str(e), False)

    stderr_lines = []
    def _drain_stderr():
        try:
            for line in process.stderr:
                print(line.rstrip())
                stderr_lines.append(line)
        except Exception:
            pass
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    print("[AI]")
    stderr_thread.start()

    output_lines = []
    try:
        for line in process.stdout:
            print(line.rstrip())
            output_lines.append(line)
    except Exception:
        pass

    stderr_thread.join(timeout=10)
    process.wait(timeout=timeout)
    if process.returncode != 0:
        err = "".join(stderr_lines)
        return (err or "AI subprocess failed", False)
    return (''.join(output_lines), True)


# [Auto Texture] AI adjustment - apply AI result to matches on main thread - Modified: 2026-08-16
def _apply_ai_result_to_matches(texture_matches, ai_result):
    """Apply AI result to texture_matches on main thread."""
    name_to_item = {item.material_name: item for item in texture_matches}
    for mat_name, mat_data in ai_result.items():
        item = name_to_item.get(mat_name)
        if item is None:
            continue
        for tex_type, path in mat_data.items():
            for attr, tt in zip(_TEX_ATTRS, _TEX_TYPES):
                if tt == tex_type:
                    setattr(item, attr, path)
                    break


# [Auto Texture] AI adjustment - thread executor - Modified: 2026-08-16
def _execute_ai_adjustment_thread(submission_data, placeholder_map, prompt, model_path, texture_matches_ref, scene_name=None):
    """Execute AI adjustment in a background thread.

    Uses bpy.app.timers to schedule main-thread updates.
    AI echo is printed to console/log, not written to ai_prompt.
    """
    def _reset_idle():
        try:
            if scene_name and scene_name in bpy.data.scenes:
                bpy.data.scenes[scene_name].node_runner.ai_state = "IDLE"
        except Exception:
            pass
        return None

    try:
        python_path = _resolve_runtime_python()
        script_path = _resolve_ai_script()

        if python_path is None:
            print(f"[[AI Adjustment]] {i18n.get_text('message.ai_runtime_missing')}")
            bpy.app.timers.register(_reset_idle, first_interval=0)
            return

        if script_path is None:
            print(f"[AI Adjustment] {i18n.get_text('message.ai_script_missing')}")
            bpy.app.timers.register(_reset_idle, first_interval=0)
            return

        submission_json = json.dumps(submission_data, ensure_ascii=False)
        print(f"[AI Adjustment] Submission data:\n{submission_json}")
        print(f"[AI Adjustment] Placeholder map:\n{json.dumps(placeholder_map, ensure_ascii=False, indent=2)}")
        #print(f"[AI Adjustment] Using model: {model_path}")
        output, success = _run_ai_subprocess(python_path, script_path, submission_json, prompt, model_path)

        if not success:
            print(f"[AI Adjustment] Error: {output}")
            bpy.app.timers.register(_reset_idle, first_interval=0)
            return

        #print(f"[AI Adjustment] AI output:\n{output}")

        try:
            ai_result = _parse_ai_return_data(output, submission_data, placeholder_map)
        except ValueError as e:
            print(f"[AI Adjustment] Parse failed: {e}")
            print(f"[AI Adjustment] Raw output:\n{output}")
            bpy.app.timers.register(_reset_idle, first_interval=0)
            return

        #print(f"[AI Adjustment] Parsed result:\n{json.dumps(ai_result, ensure_ascii=False, indent=2)}")

        def _apply_result():
            try:
                if scene_name and scene_name in bpy.data.scenes:
                    scene = bpy.data.scenes[scene_name]
                    _apply_ai_result_to_matches(scene.node_runner.texture_matches, ai_result)
                    scene.node_runner.ai_state = "IDLE"
            except Exception as e:
                print(f"[AI Adjustment] Failed to apply result: {e}")
            return None
        bpy.app.timers.register(_apply_result, first_interval=0)

    except Exception as e:
        import traceback
        print(f"[AI Adjustment] Thread crashed: {e}")
        traceback.print_exc()
        bpy.app.timers.register(_reset_idle, first_interval=0)


# [Auto Texture] AI adjustment operator - Modified: 2026-08-16
class NODE_OT_ai_adjust(bpy.types.Operator):
    """AI adjust matching results"""

    bl_idname = _pkg + ".ai_adjust"
    bl_label = "AI调整"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.scene.node_runner.ai_state != "ADJUSTING"

    def execute(self, context):
        scene = context.scene
        node_runner = scene.node_runner

        if not node_runner.texture_matches:
            self.report({"WARNING"}, i18n.get_text("message.ai_no_match"))
            return {"CANCELLED"}

        if node_runner.ai_state == "ADJUSTING":
            return {"CANCELLED"}

        ai_model = node_runner.ai_model
        model_path = _resolve_selected_model_path(ai_model)
        if model_path is None:
            models = _scan_gguf_models()
            if models and models[0][0] != "none":
                node_runner.ai_model = models[0][0]
                model_path = _resolve_selected_model_path(node_runner.ai_model)
            if model_path is None:
                self.report({"WARNING"}, i18n.get_text("message.ai_no_available_model"))
                return {"CANCELLED"}

        _root_dir = bpy.path.abspath(node_runner.texture_directory) if node_runner.texture_directory else None
        submission_data, placeholder_map = _build_ai_submission_data(
            node_runner.texture_matches, root_dir=_root_dir
        )

        has_data = False
        for _mat, _md in submission_data.items():
            if not isinstance(_md, dict):
                continue
            for _k, _v in _md.items():
                if _k == "unmatch":
                    if _v:
                        has_data = True
                        break
                elif isinstance(_v, str) and _v:
                    has_data = True
                    break
            if has_data:
                break
        if not has_data:
            self.report({"WARNING"}, i18n.get_text("message.ai_no_data"))
            return {"CANCELLED"}

        node_runner.ai_state = "ADJUSTING"

        prompt = node_runner.ai_prompt

        thread = threading.Thread(
            target=_execute_ai_adjustment_thread,
            args=(submission_data, placeholder_map, prompt, model_path, node_runner.texture_matches, scene.name),
            daemon=True,
        )
        thread.start()

        return {"FINISHED"}


class NodeRunnerProperties(bpy.types.PropertyGroup):
    texture_directory: bpy.props.StringProperty(
        name="",
        description="Resource directory for textures (defaults to blend file directory)",
        default="",
        maxlen=1024,
        subtype="DIR_PATH",
    )
    texture_matches: bpy.props.CollectionProperty(
        name="Texture Matches",
        type=TextureMatchItem,
    )
    ai_prompt: bpy.props.StringProperty(
        name="AI Prompt",
        default="",
        maxlen=8192,
    )
    ai_state: bpy.props.EnumProperty(
        name="AI State",
        items=[("IDLE", "空闲", ""), ("ADJUSTING", "调整中", "")],
        default="IDLE",
    )
    ai_model: bpy.props.EnumProperty(
        name="AI Model",
        items=_scan_gguf_models
    )


def menu_draw(self, context):
    if not _supported_editor_poll(context):
        return
    self.layout.separator()
    self.layout.menu(NODE_RUNNER_MT_menu.bl_idname, icon="NODE")


# Registration


_classes = (
    AUTO_NODE_RUNNER_preferences,
    NODE_RUNNER_OT_export_clipboard,
    NODE_RUNNER_OT_export_file,
    NODE_RUNNER_OT_confirm_import,
    NODE_RUNNER_OT_import_clipboard,
    NODE_RUNNER_OT_import_file,
    NODE_RUNNER_MT_menu,
    NODE_OT_match_textures,
    NODE_OT_apply_textures,
    NODE_OT_clear_texture_matches,
    NODE_OT_select_texture_directory,
    NODE_OT_toggle_collapse,
    NODE_OT_set_texture_directory,
    NODE_OT_ai_adjust,
    NODE_OT_uninstall_addon,
    TextureMatchItem,
    NodeRunnerProperties,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.node_runner = bpy.props.PointerProperty(type=NodeRunnerProperties)

    bpy.types.NODE_MT_context_menu.append(menu_draw)


def unregister():
    bpy.types.NODE_MT_context_menu.remove(menu_draw)

    # Unregister properties
    del bpy.types.Scene.node_runner

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
