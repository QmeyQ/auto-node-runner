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


def _get_prefs(context):
    """Return addon preferences, with safe fallback defaults."""
    prefs = context.preferences.addons.get(__package__)
    if prefs:
        return prefs.preferences
    return None


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

    bl_idname = _pkg + "_menu"
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
        "basecolor": {"patterns": ["basecolor", "albedo", "diffuse", "color", "base", "bc"]},
        "metallic": {"patterns": ["metallic", "metal", "metalness", "mt"]},
        "roughness": {"patterns": ["roughness", "rough", "rgh"]},
        "normal": {"patterns": ["normal", "norm", "nrm", "nor"]},
        "ao": {"patterns": ["ambient_occlusion", "ambientocclusion", "ao", "occlusion", "occ"]},
    },
    "image_extensions": [".png", ".jpg", ".jpeg", ".tga", ".exr", ".tif", ".tiff"],
}


# [Auto Texture] Compile match rules from loaded data into regex patterns - Modified: 2026-06-09
def _get_match_rules():
    """Get compiled match rules, loading from file if available."""
    data = _load_match_rules()
    if data is None:
        data = _DEFAULT_MATCH_RULES

    rules = data.get("rules", {})
    ext_list = data.get("image_extensions", [".png", ".jpg", ".jpeg", ".tga", ".exr", ".tif", ".tiff"])
    image_extensions = frozenset(ext_list)

    type_patterns = {}
    for tex_type, rule_data in rules.items():
        patterns = rule_data.get("patterns", [])
        if patterns:
            type_patterns[tex_type] = r"(" + "|".join(re.escape(p) for p in patterns) + ")"

    return type_patterns, image_extensions


# [Auto Texture] Match texture files to material using fuzzy name + type keyword patterns - Modified: 2026-06-09
def _match_textures_for_material(mat_name, texture_files):
    """Match texture files to a material based on fuzzy name matching."""
    type_patterns, image_extensions = _get_match_rules()
    mat_pattern = _build_mat_name_pattern(mat_name)
    matches = {tex_type: [] for tex_type in type_patterns}

    ext_pattern = r"\.(" + "|".join(re.escape(e.lstrip(".")) for e in image_extensions) + ")"

    print(f"[AutoTexture] match: mat_name={mat_name}, mat_pattern={mat_pattern}")

    for tex_type, type_kw in type_patterns.items():
        if mat_pattern:
            full_pattern = mat_pattern + r"[^/\\]*?" + type_kw + r"[^/\\]*?" + ext_pattern
        else:
            full_pattern = type_kw + r"[^/\\]*?" + ext_pattern

        pattern = re.compile(full_pattern, re.IGNORECASE)

        for tex_path in texture_files:
            filename = os.path.basename(tex_path)
            if pattern.search(filename):
                matches[tex_type].append(tex_path)

        matches[tex_type].sort(key=lambda p: len(os.path.basename(p)))

        if matches[tex_type]:
            print(f"[AutoTexture] match: {tex_type} -> {matches[tex_type][0]}")

    return matches, image_extensions


# [Auto Texture] Scan directory for image files using configurable extensions - Modified: 2026-06-09
def _scan_directory_for_textures(directory, image_extensions=None):
    """Scan a directory recursively for image files."""
    if image_extensions is None:
        _, image_extensions = _get_match_rules()
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

        if not node_runner.texture_directory:
            self.report({"WARNING"}, i18n.get_text("message.select_directory_first"))
            return {"CANCELLED"}

        tex_dir = node_runner.texture_directory

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

        auto_matches = {}
        for mat in materials:
            tex_map, _ = _match_textures_for_material(mat.name, textures)
            auto_matches[mat.name] = {
                "basecolor_path": (tex_map.get("basecolor") or [""])[0],
                "metallic_path": (tex_map.get("metallic") or [""])[0],
                "roughness_path": (tex_map.get("roughness") or [""])[0],
                "normal_path": (tex_map.get("normal") or [""])[0],
                "ao_path": (tex_map.get("ao") or [""])[0],
            }

        history_data = history.load_texture_matches(context)
        current_mat_names = {mat.name for mat in materials}
        merged = history.merge_with_history(auto_matches, history_data, current_mat_names)

        for mat_name, paths in merged.items():
            item = node_runner.texture_matches.add()
            item.material_name = mat_name
            item.basecolor_path = paths.get("basecolor_path", "")
            item.metallic_path = paths.get("metallic_path", "")
            item.roughness_path = paths.get("roughness_path", "")
            item.normal_path = paths.get("normal_path", "")
            item.ao_path = paths.get("ao_path", "")

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

        addon_dir = os.path.dirname(os.path.abspath(__file__))
        ao_template_path = os.path.join(addon_dir, "ao.json")
        noao_template_path = os.path.join(addon_dir, "noao.json")

        print(f"[AutoTexture] apply: loading templates from {addon_dir}")

        try:
            with open(ao_template_path, "r", encoding="utf-8") as f:
                ao_template = json.load(f)
            with open(noao_template_path, "r", encoding="utf-8") as f:
                noao_template = json.load(f)
        except Exception as e:
            self.report({"ERROR"}, f"Template load failed: {e}")
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

            has_ao = item.ao_path and os.path.exists(item.ao_path)
            template = copy.deepcopy(ao_template if has_ao else noao_template)

            print(f"[AutoTexture] apply: mat={item.material_name}, has_ao={has_ao}, template={'ao' if has_ao else 'noao'}")

            _apply_template_to_material(mat, item, template, has_ao)
            applied_count += 1

        for item in node_runner.texture_matches:
            for attr in ("basecolor_path", "metallic_path", "roughness_path", "normal_path", "ao_path"):
                path = getattr(item, attr)
                if path and not os.path.exists(path):
                    missing_count += 1

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


# [Auto Texture] Map template Image Texture node names to texture types (ao/noao variants) - Modified: 2026-06-09
_TEXTURE_NODE_MAP = {
    "basecolor": {
        "ao": ("Image Texture.001", "Base Color"),
        "noao": ("Image Texture.001", "Base Color"),
    },
    "metallic": {
        "ao": ("Image Texture.002", "Metallic"),
        "noao": ("Image Texture.002", "Metallic"),
    },
    "roughness": {
        "ao": ("Image Texture.004", "Roughness"),
        "noao": ("Image Texture.004", "Roughness"),
    },
    "normal": {
        "ao": ("Image Texture.003", "Normal"),
        "noao": ("Image Texture.003", "Normal"),
    },
    "ao": {
        "ao": ("Image Texture", "AO"),
        "noao": (None, None),
    },
}

# [Auto Texture] Map texture types to Group Output socket identifiers for top-level link removal - Modified: 2026-06-09
_GROUP_OUTPUT_SOCKET_MAP = {
    "basecolor": "Socket_0",
    "metallic": "Socket_1",
    "roughness": "Socket_2",
    "normal": "Socket_3",
}


# [Auto Texture] Rewrite template JSON image paths/names to matched texture file paths - Modified: 2026-06-09
def _rewrite_template_paths(template, item, has_ao):
    template_key = "ao" if has_ao else "noao"

    nodes = template.get("nodes", {})
    group_node = None
    for key, nd in nodes.items():
        if nd.get("type") == "ShaderNodeGroup":
            group_node = nd
            break

    if not group_node:
        print(f"[AutoTexture] ERROR: No ShaderNodeGroup found in template nodes: {list(nodes.keys())}")
        return set()

    sub_tree = group_node.get("node_tree", {})
    sub_nodes = sub_tree.get("nodes", {})

    mat_name = getattr(item, "material_name", "")
    safe_mat = re.sub(r"[^\w]", "_", mat_name)[:32]

    path_map = {
        "basecolor": getattr(item, "basecolor_path", ""),
        "metallic": getattr(item, "metallic_path", ""),
        "roughness": getattr(item, "roughness_path", ""),
        "normal": getattr(item, "normal_path", ""),
        "ao": getattr(item, "ao_path", ""),
    }

    print(f"[AutoTexture] rewrite_paths: mat={mat_name}, has_ao={has_ao}, template_key={template_key}")
    print(f"[AutoTexture] rewrite_paths: sub_nodes keys={list(sub_nodes.keys())}")
    print(f"[AutoTexture] rewrite_paths: path_map={path_map}")

    missing_types = set()

    for tex_type, info in _TEXTURE_NODE_MAP.items():
        node_name, socket_name = info.get(template_key, (None, None))
        if node_name is None:
            continue

        node_data = sub_nodes.get(node_name, {})
        img_data = node_data.get("image", {})

        print(f"[AutoTexture]   {tex_type}: node_name={node_name}, found={bool(node_data)}, has_image={bool(img_data)}")

        if not img_data:
            print(f"[AutoTexture]   {tex_type}: SKIPPED - no image data in node")
            continue

        file_path = path_map.get(tex_type, "")
        if file_path and os.path.exists(file_path):
            img_data["filepath"] = file_path
            base_name = os.path.basename(file_path)
            name_part = os.path.splitext(base_name)[0]
            ext_part = os.path.splitext(base_name)[1]
            img_data["name"] = f"{name_part}_{safe_mat}{ext_part}"
            print(f"[AutoTexture]   {tex_type}: SET filepath={file_path}, name={img_data['name']}")
        else:
            img_data["filepath"] = ""
            img_data["name"] = ""
            missing_types.add(tex_type)
            print(f"[AutoTexture]   {tex_type}: MISSING - path={file_path}")

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

    print(f"[AutoTexture] rename_nodes: mat={mat_name}, group_key={group_key} -> {new_group_name}")

    return new_group_name


# [Auto Texture] Remove top-level Group->BSDF links for missing textures (disconnects unmatched) - Modified: 2026-06-09
def _remove_missing_links(template, missing_types, has_ao, group_node_name):
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


# [Auto Texture] Apply template to material: rewrite paths, rename nodes, remove links, clear old, deserialize - Modified: 2026-06-09
def _apply_template_to_material(mat, item, template, has_ao):
    print(f"[AutoTexture] apply_template: mat={mat.name}, has_ao={has_ao}")

    missing_types = _rewrite_template_paths(template, item, has_ao)
    print(f"[AutoTexture] apply_template: missing_types={missing_types}")

    group_node_name = _rename_template_nodes(template, mat.name)

    _remove_missing_links(template, missing_types, has_ao, group_node_name)
    print(f"[AutoTexture] apply_template: top_links after removal={len(template.get('links', []))}")

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

    for tex_type in ("basecolor", "metallic", "roughness", "normal", "ao"):
        path = getattr(item, f"{tex_type}_path", "")
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
        print(f"[AutoTexture] apply_template: deserialize SUCCESS for mat={mat.name}")
        for node in node_tree.nodes:
            if hasattr(node, "image") and node.image:
                print(f"[AutoTexture]   node={node.name}, image={node.image.name}, filepath={node.image.filepath}")
    except Exception as e:
        print(f"[AutoTexture] apply_template: deserialize FAILED for mat={mat.name}: {e}")
        log.warning(f"Failed to deserialize template for material '{mat.name}': {e}")
        mat.use_nodes = True
        return


class TextureMatchItem(bpy.types.PropertyGroup):
    material_name: bpy.props.StringProperty(name="Material Name")
    basecolor_path: bpy.props.StringProperty(name="Base Color Path", subtype="FILE_PATH")
    metallic_path: bpy.props.StringProperty(name="Metallic Path", subtype="FILE_PATH")
    roughness_path: bpy.props.StringProperty(name="Roughness Path", subtype="FILE_PATH")
    normal_path: bpy.props.StringProperty(name="Normal Path", subtype="FILE_PATH")
    ao_path: bpy.props.StringProperty(name="AO Path", subtype="FILE_PATH")


def _on_texture_path_update(self, context):
    print(f"[AutoTexture] path_updated: mat={getattr(self, 'material_name', '?')}")
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


class NodeRunnerProperties(bpy.types.PropertyGroup):
    texture_directory: bpy.props.StringProperty(
        name="Texture Directory",
        description="Directory containing texture files",
        default="",
        maxlen=1024,
        subtype="DIR_PATH",
    )
    texture_matches: bpy.props.CollectionProperty(
        name="Texture Matches",
        type=TextureMatchItem,
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
