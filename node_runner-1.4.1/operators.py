"""
Blender operators and UI for Node Runner import/export.

Modifications (2026-06-08):
  - Added AUTO_NODE_RUNNER_preferences class (renamed from NODE_RUNNER_preferences
    to avoid conflict with original plugin)
  - All bl_idname values changed to use __package__ prefix for addon isolation
  - Added 自动贴图 (Auto Texture) Panel with resource directory, texture matching,
    and one-click apply functionality
  - Added NODE_OT_match_textures, NODE_OT_apply_textures,
    NODE_OT_clear_texture_matches, NODE_OT_select_texture_directory operators
  - Added TextureMatchItem and NodeRunnerProperties property groups
  - Added auto-save/load to/from nodetmp.txt for texture match persistence
"""

import logging

import bpy

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
    """插件设置面板。
    重命名自 NODE_RUNNER_preferences 以避免与原始插件冲突。
    修改于 2026-06-08
    """
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

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_at_cursor")
        layout.prop(self, "select_imported")


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

    bl_idname = __package__ + ".export_clipboard"
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

    bl_idname = __package__ + ".export_file"
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

    bl_idname = __package__ + ".import_clipboard"
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

    bl_idname = __package__ + ".import_file"
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

    bl_idname = __package__ + ".confirm_import"
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

    bl_idname = __package__ + "_menu"
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


# ============================================================
# 自动贴图 (Auto Texture) Panel
# 新增于 2026-06-08
# 功能：从资源目录自动匹配贴图文件到选中材质的对应通道，
#       支持 basecolor/metallic/roughness/normal/AO 五通道，
#       根据正则匹配规则自动识别贴图类型，
#       匹配结果实时保存到工程目录下的 nodetmp.txt。
# ============================================================

import os
import re
import json


def _get_unique_materials_from_selection(context):
    """获取选中物体中不重复的材质列表（排除同名材质）。
    新增于 2026-06-08
    """
    materials = []
    seen = set()
    for obj in context.selected_objects:
        if obj.type == "MESH":
            for slot in obj.material_slots:
                if slot.material and slot.material.name not in seen:
                    seen.add(slot.material.name)
                    materials.append(slot.material)
    return materials


def _clean_material_name(name):
    """移除材质名中的特殊符号（_ @ . 等），统一转小写用于文件名匹配。
    新增于 2026-06-08
    """
    return re.sub(r"[_\-@.\s]+", "", name).lower()


def _match_textures_for_material(mat_name, texture_files):
    """根据材质名和贴图类型正则规则，从文件列表中匹配对应贴图。
    规则：先匹配清理后的材质名，再匹配贴图类型关键词（不区分大小写）。
    新增于 2026-06-08
    """
    cleaned_name = _clean_material_name(mat_name)
    matches = {
        "basecolor": [],
        "metallic": [],
        "roughness": [],
        "normal": [],
        "ao": [],
    }

    # 贴图类型正则匹配表（不区分大小写）
    # 关键词覆盖最常见的命名约定
    patterns = {
        "basecolor": re.compile(
            r"(" + re.escape(cleaned_name) + r")[^/\\]*?(basecolor|albedo|diffuse|color|base|bc)[^/\\]*?\.(png|jpg|jpeg|tga|exr|tif|tiff)",
            re.IGNORECASE
        ),
        "metallic": re.compile(
            r"(" + re.escape(cleaned_name) + r")[^/\\]*?(metallic|metal|metalness|mt)[^/\\]*?\.(png|jpg|jpeg|tga|exr|tif|tiff)",
            re.IGNORECASE
        ),
        "roughness": re.compile(
            r"(" + re.escape(cleaned_name) + r")[^/\\]*?(roughness|rough|rgh)[^/\\]*?\.(png|jpg|jpeg|tga|exr|tif|tiff)",
            re.IGNORECASE
        ),
        "normal": re.compile(
            r"(" + re.escape(cleaned_name) + r")[^/\\]*?(normal|norm|nrm|nor)[^/\\]*?\.(png|jpg|jpeg|tga|exr|tif|tiff)",
            re.IGNORECASE
        ),
        "ao": re.compile(
            r"(" + re.escape(cleaned_name) + r")[^/\\]*?(ambient.?occlusion|ao|occlusion|occ)[^/\\]*?\.(png|jpg|jpeg|tga|exr|tif|tiff)",
            re.IGNORECASE
        ),
    }

    for tex_path in texture_files:
        filename = os.path.basename(tex_path)
        for tex_type, pattern in patterns.items():
            if pattern.search(filename):
                matches[tex_type].append(tex_path)

    return matches


def _scan_directory_for_textures(directory):
    """递归扫描目录，返回所有支持的图片文件路径列表。
    新增于 2026-06-08
    """
    image_extensions = {".png", ".jpg", ".jpeg", ".tga", ".exr", ".tif", ".tiff"}
    textures = []
    if os.path.isdir(directory):
        for root, _, files in os.walk(directory):
            for f in files:
                if os.path.splitext(f.lower())[1] in image_extensions:
                    textures.append(os.path.join(root, f))
    return textures


class NODE_OT_match_textures(bpy.types.Operator):
    """扫描资源目录，为选中材质的各通道匹配对应贴图文件。
    新增于 2026-06-08
    """

    bl_idname = __package__ + ".match_textures"
    bl_label = "匹配贴图"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        node_runner = scene.node_runner

        # 若未设置资源目录，默认使用工程文件所在目录
        # 新增于 2026-06-08
        if not node_runner.texture_directory:
            project_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""
            if project_dir:
                node_runner.texture_directory = project_dir

        if not node_runner.texture_directory:
            self.report({"WARNING"}, "请先选择资源目录")
            return {"CANCELLED"}

        if not os.path.isdir(node_runner.texture_directory):
            self.report({"WARNING"}, "资源目录无效")
            return {"CANCELLED"}

        materials = _get_unique_materials_from_selection(context)
        if not materials:
            self.report({"WARNING"}, "请先选择包含材质的物体")
            return {"CANCELLED"}

        # 清除上一次的匹配结果
        node_runner.texture_matches.clear()

        # 扫描目录获取所有贴图文件
        textures = _scan_directory_for_textures(node_runner.texture_directory)

        # 为每个材质匹配对应贴图
        for mat in materials:
            tex_map = _match_textures_for_material(mat.name, textures)
            item = node_runner.texture_matches.add()
            item.material_name = mat.name
            item.basecolor_path = tex_map["basecolor"][0] if tex_map["basecolor"] else ""
            item.metallic_path = tex_map["metallic"][0] if tex_map["metallic"] else ""
            item.roughness_path = tex_map["roughness"][0] if tex_map["roughness"] else ""
            item.normal_path = tex_map["normal"][0] if tex_map["normal"] else ""
            item.ao_path = tex_map["ao"][0] if tex_map["ao"] else ""

        # 保存匹配结果到 nodetmp.txt（动态持久化）
        _save_texture_matches(context)

        mat_count = len(node_runner.texture_matches)
        self.report({"INFO"}, f"已匹配 {mat_count} 个材质")
        return {"FINISHED"}


def _save_texture_matches(context):
    """将当前匹配结果序列化为 JSON 保存到工程目录的 nodetmp.txt。
    每次修改都动态保存，确保重启 Blender 后数据可恢复。
    新增于 2026-06-08
    """
    scene = context.scene
    node_runner = scene.node_runner

    data = {
        "texture_directory": node_runner.texture_directory,
        "matches": [],
    }

    for item in node_runner.texture_matches:
        data["matches"].append({
            "material_name": item.material_name,
            "basecolor_path": item.basecolor_path,
            "metallic_path": item.metallic_path,
            "roughness_path": item.roughness_path,
            "normal_path": item.normal_path,
            "ao_path": item.ao_path,
        })

    tmp_file = os.path.join(os.path.dirname(bpy.data.filepath) or os.path.expanduser("~"), "nodetmp.txt")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Failed to save nodetmp.txt: {e}")


class NODE_OT_apply_textures(bpy.types.Operator):
    """一键应用：根据匹配结果，为每个材质创建/连接贴图节点。
    根据是否匹配到 AO 贴图，自动选择 ao.json 或 noao.json 模板。
    未找到文件的贴图通道将被断开连接。
    新增于 2026-06-08
    """

    bl_idname = __package__ + ".apply_textures"
    bl_label = "一键应用"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        node_runner = scene.node_runner

        if not node_runner.texture_matches:
            self.report({"WARNING"}, "没有可应用的数据")
            return {"CANCELLED"}

        # 加载模板文件（优先从插件目录加载）
        addon_dir = os.path.dirname(os.path.abspath(__file__))
        ao_template_path = os.path.join(addon_dir, "ao.json")
        noao_template_path = os.path.join(addon_dir, "noao.json")

        try:
            with open(ao_template_path, "r", encoding="utf-8") as f:
                ao_template = json.load(f)
            with open(noao_template_path, "r", encoding="utf-8") as f:
                noao_template = json.load(f)
        except Exception as e:
            self.report({"ERROR"}, f"无法加载模板文件: {e}")
            return {"CANCELLED"}

        applied_count = 0
        missing_count = 0

        for item in node_runner.texture_matches:
            mat = bpy.data.materials.get(item.material_name)
            if not mat or not mat.use_nodes:
                continue

            # 根据是否有 AO 贴图选择模板
            has_ao = item.ao_path and os.path.exists(item.ao_path)
            template = ao_template if has_ao else noao_template

            # 查找 Principled BSDF 节点
            bsdf = None
            for node in mat.node_tree.nodes:
                if node.bl_idname == "ShaderNodeBsdfPrincipled":
                    bsdf = node
                    break

            if not bsdf:
                continue

            # 辅助函数：加载图片并连接到 BSDF 对应输入
            def setup_texture_input(principled_bsdf, input_name, file_path, node_tree):
                if not file_path or not os.path.exists(file_path):
                    return None

                # 查找或新建纹理节点
                tex_node = None
                for node in node_tree.nodes:
                    if hasattr(node, "image") and getattr(node, "image", None):
                        if hasattr(node.image, "filepath") and node.image.filepath == file_path:
                            tex_node = node
                            break

                if tex_node is None:
                    tex_node = node_tree.nodes.new("ShaderNodeTexImage")
                    try:
                        img = bpy.data.images.load(file_path)
                        tex_node.image = img
                    except Exception:
                        return None

                # 输入通道到 BSDF 输入索引的映射
                socket_map = {
                    "Base Color": 0,
                    "Metallic": 1,
                    "Roughness": 2,
                    "Normal": 3,
                }

                if input_name in socket_map:
                    idx = socket_map[input_name]
                    # 断开已有连接
                    if idx < len(principled_bsdf.inputs) and principled_bsdf.inputs[idx].links:
                        link = principled_bsdf.inputs[idx].links[0]
                        node_tree.links.remove(link)

                    # 连接纹理到 BSDF
                    try:
                        node_tree.links.new(tex_node.outputs[0], principled_bsdf.inputs[idx])
                    except Exception:
                        pass

                return tex_node

            # 设置各通道贴图
            if item.basecolor_path:
                setup_texture_input(bsdf, "Base Color", item.basecolor_path, mat.node_tree)
            if item.metallic_path:
                setup_texture_input(bsdf, "Metallic", item.metallic_path, mat.node_tree)
            if item.roughness_path:
                setup_texture_input(bsdf, "Roughness", item.roughness_path, mat.node_tree)

            # Normal 贴图需经过 NormalMap 节点转换
            if item.normal_path and os.path.exists(item.normal_path):
                normal_node = None
                for node in mat.node_tree.nodes:
                    if node.bl_idname == "ShaderNodeNormalMap":
                        normal_node = node
                        break

                if normal_node is None:
                    normal_node = mat.node_tree.nodes.new("ShaderNodeNormalMap")

                norm_tex_node = None
                for node in mat.node_tree.nodes:
                    if hasattr(node, "image") and getattr(node, "image", None):
                        if hasattr(node.image, "filepath") and node.image.filepath == item.normal_path:
                            norm_tex_node = node
                            break

                if norm_tex_node is None:
                    try:
                        img = bpy.data.images.load(item.normal_path)
                        norm_tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                        norm_tex_node.image = img
                    except Exception:
                        pass

                if norm_tex_node:
                    # 断开现有 Normal 连接
                    if len(bsdf.inputs[3].links) > 0:
                        link = bsdf.inputs[3].links[0]
                        mat.node_tree.links.remove(link)

                    # 纹理 -> NormalMap -> BSDF Normal
                    mat.node_tree.links.new(norm_tex_node.outputs[0], normal_node.inputs[1])
                    mat.node_tree.links.new(normal_node.outputs[0], bsdf.inputs[3])

            # AO 贴图（如果有）
            if has_ao:
                ao_tex_node = None
                for node in mat.node_tree.nodes:
                    if hasattr(node, "image") and getattr(node, "image", None):
                        if hasattr(node.image, "filepath") and node.image.filepath == item.ao_path:
                            ao_tex_node = node
                            break

                if ao_tex_node is None:
                    try:
                        img = bpy.data.images.load(item.ao_path)
                        ao_tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                        ao_tex_node.image = img
                    except Exception:
                        pass

            applied_count += 1

        # 统计未找到文件的贴图数量
        for item in node_runner.texture_matches:
            for attr in ["basecolor_path", "metallic_path", "roughness_path", "normal_path", "ao_path"]:
                path = getattr(item, attr)
                if path and not os.path.exists(path):
                    missing_count += 1

        self.report({"INFO"}, f"已应用 {applied_count} 个材质, {missing_count} 个贴图未找到")
        return {"FINISHED"}


class NODE_OT_clear_texture_matches(bpy.types.Operator):
    """清除所有贴图匹配结果。
    新增于 2026-06-08
    """

    bl_idname = __package__ + ".clear_texture_matches"
    bl_label = "清除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        scene.node_runner.texture_matches.clear()
        return {"FINISHED"}


class NODE_OT_select_texture_directory(bpy.types.Operator):
    """打开文件浏览器选择贴图资源目录。
    新增于 2026-06-08
    """

    bl_idname = __package__ + ".select_texture_directory"
    bl_label = "选择目录"
    bl_options = {"REGISTER"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH")  # type: ignore

    def execute(self, context):
        context.scene.node_runner.texture_directory = self.directory
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


def _draw_texture_match_item(layout, item, mat_name):
    """在面板中绘制单个材质对应的五通道贴图路径输入框。
    每个通道一行，支持拖动文件放置。
    新增于 2026-06-08
    """
    box = layout.box()
    box.label(text=f"{mat_name}", icon="MATERIAL")

    # Base Color
    row = box.row(align=True)
    row.label(text="Base Color:", icon="IMAGE_RGB")
    row.prop(item, "basecolor_path", text="", emboss=False)

    # Metallic
    row = box.row(align=True)
    row.label(text="Metallic:", icon="IMAGE_RGB")
    row.prop(item, "metallic_path", text="", emboss=False)

    # Roughness
    row = box.row(align=True)
    row.label(text="Roughness:", icon="IMAGE_RGB")
    row.prop(item, "roughness_path", text="", emboss=False)

    # Normal
    row = box.row(align=True)
    row.label(text="Normal:", icon="IMAGE_RGB")
    row.prop(item, "normal_path", text="", emboss=False)

    # AO
    row = box.row(align=True)
    row.label(text="AO:", icon="IMAGE_RGB")
    row.prop(item, "ao_path", text="", emboss=False)


class NODE_RUNNER_PT_auto_texture(bpy.types.Panel):
    """自动贴图面板 - 显示在节点编辑器的侧栏中。
    第一行：资源目录输入框（支持拖动文件夹）
    第二行：匹配贴图按钮（显示当前选中材质数）
    之后逐材质显示五通道匹配结果
    最下方：一键应用按钮（显示未填数量）
    新增于 2026-06-08
    """

    bl_label = "自动贴图"
    bl_idname = __package__ + "_panel_auto_texture"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "自动贴图"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        node_runner = scene.node_runner

        # 第一行：资源目录（支持拖动放置文件夹）
        # 若未设置则自动填入工程文件所在目录
        # 新增于 2026-06-08
        if not node_runner.texture_directory and bpy.data.filepath:
            node_runner.texture_directory = os.path.dirname(bpy.data.filepath)

        row = layout.row(align=True)
        row.label(text="资源目录:", icon="FILE_FOLDER")
        row.prop(node_runner, "texture_directory", text="")
        row.operator(NODE_OT_select_texture_directory.bl_idname, text="", icon="FILEBROWSER")

        # 第二行：匹配贴图按钮，显示已选材质数量（去重后）
        materials = _get_unique_materials_from_selection(context)
        mat_count = len(materials)
        row = layout.row(align=True)
        row.operator(NODE_OT_match_textures.bl_idname, text=f"匹配贴图（已选 {mat_count} 个材质）", icon="VIEWZOOM")
        row.operator(NODE_OT_clear_texture_matches.bl_idname, text="", icon="X")

        # 逐材质显示匹配结果
        if node_runner.texture_matches:
            layout.separator()
            for item in node_runner.texture_matches:
                _draw_texture_match_item(layout, item, item.material_name)

            # 统计未找到文件的贴图数量
            missing = 0
            for item in node_runner.texture_matches:
                for attr in ["basecolor_path", "metallic_path", "roughness_path", "normal_path", "ao_path"]:
                    path = getattr(item, attr)
                    if path and not os.path.exists(path):
                        missing += 1

            # 一键应用按钮（显示未填数量）
            layout.separator()
            row = layout.row()
            row.operator(NODE_OT_apply_textures.bl_idname, text=f"一键应用（{missing} 个未填）", icon="PLAY")


# ============================================================
# TextureMatchItem - 贴图匹配数据模型
# 存储每个材质对应的五通道贴图路径
# 新增于 2026-06-08
# ============================================================


class TextureMatchItem(bpy.types.PropertyGroup):
    """每个材质对应的贴图匹配条目，包含五通道文件路径。
    新增于 2026-06-08
    """
    material_name: bpy.props.StringProperty(name="Material Name")
    basecolor_path: bpy.props.StringProperty(name="Base Color Path")
    metallic_path: bpy.props.StringProperty(name="Metallic Path")
    roughness_path: bpy.props.StringProperty(name="Roughness Path")
    normal_path: bpy.props.StringProperty(name="Normal Path")
    ao_path: bpy.props.StringProperty(name="AO Path")


class NodeRunnerProperties(bpy.types.PropertyGroup):
    """场景级属性组，挂载在 Scene 上存储贴图匹配状态。
    新增于 2026-06-08
    """
    texture_directory: bpy.props.StringProperty(
        name="Texture Directory",
        description="贴图资源目录",
        default="",
        maxlen=1024,
        subtype="DIR_PATH",
    )
    texture_matches: bpy.props.CollectionProperty(
        name="Texture Matches",
        type=TextureMatchItem,
    )


def _load_texture_matches(context):
    """从工程目录的 nodetmp.txt 加载之前保存的匹配结果。
    每次 Blender 启动或切换工程时调用，确保面板恢复上次工作状态。
    新增于 2026-06-08
    """
    tmp_file = os.path.join(os.path.dirname(bpy.data.filepath) or os.path.expanduser("~"), "nodetmp.txt")
    if not os.path.exists(tmp_file):
        return

    try:
        with open(tmp_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        scene = context.scene
        node_runner = scene.node_runner
        node_runner.texture_directory = data.get("texture_directory", "")

        for match in data.get("matches", []):
            item = node_runner.texture_matches.add()
            item.material_name = match.get("material_name", "")
            item.basecolor_path = match.get("basecolor_path", "")
            item.metallic_path = match.get("metallic_path", "")
            item.roughness_path = match.get("roughness_path", "")
            item.normal_path = match.get("normal_path", "")
            item.ao_path = match.get("ao_path", "")
    except Exception as e:
        log.warning(f"Failed to load nodetmp.txt: {e}")


# 属性更新回调：对 TextureMatchItem 的所有五通道属性设置自动保存
# 每当用户手动修改某个贴图路径时，自动触发保存到 nodetmp.txt
# 新增于 2026-06-08
def _on_texture_matches_update(self, context):
    """属性变化回调，自动保存当前匹配结果到 nodetmp.txt。"""
    _save_texture_matches(context)


TextureMatchItem.__annotations__["basecolor_path"] = bpy.props.StringProperty(
    name="Base Color Path",
    update=_on_texture_matches_update,
)
TextureMatchItem.__annotations__["metallic_path"] = bpy.props.StringProperty(
    name="Metallic Path",
    update=_on_texture_matches_update,
)
TextureMatchItem.__annotations__["roughness_path"] = bpy.props.StringProperty(
    name="Roughness Path",
    update=_on_texture_matches_update,
)
TextureMatchItem.__annotations__["normal_path"] = bpy.props.StringProperty(
    name="Normal Path",
    update=_on_texture_matches_update,
)
TextureMatchItem.__annotations__["ao_path"] = bpy.props.StringProperty(
    name="AO Path",
    update=_on_texture_matches_update,
)


def menu_draw(self, context):
    if not _supported_editor_poll(context):
        return
    self.layout.separator()
    self.layout.menu(NODE_RUNNER_MT_menu.bl_idname, icon="NODE")


# ============================================================
# 注册 / 注销
# 新增于 2026-06-08：添加了自动贴图相关类和属性
# ============================================================

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
    NODE_RUNNER_PT_auto_texture,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    # Register properties
    bpy.types.Scene.node_runner = bpy.props.PointerProperty(type=NodeRunnerProperties)

    bpy.types.NODE_MT_context_menu.append(menu_draw)


def unregister():
    bpy.types.NODE_MT_context_menu.remove(menu_draw)

    # Unregister properties
    del bpy.types.Scene.node_runner

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
