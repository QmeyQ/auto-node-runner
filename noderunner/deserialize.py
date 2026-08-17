"""
Deserialization module for Node Runner.

Recreates Blender node trees from plain Python dicts produced by
the serialization module.
"""

import logging

import bpy

from .constants import (
    READONLY_DESERIALIZE_PROPS,
    SOCKET_BASE_TYPES,
    MODE_CHANGING_PROPS,
    PAIRED_NODE_TYPES,
)

log = logging.getLogger(__name__)

# Helpers


def get_node_socket_base_type(socket_type: str) -> str:
    """Map a specific socket type string to its base type.

    Only base types can be used with ``NodeTreeInterface.new_socket()``.
    Returns ``'NodeSocketFloat'`` as last-resort fallback.
    """
    for base in SOCKET_BASE_TYPES:
        if base in socket_type:
            return base
    return "NodeSocketFloat"


def get_socket_by_identifier(
    node, identifier, socket_id_map, direction="INPUT", name=None
):
    """Find a socket on *node* by its original identifier.

    Handles remapping for group nodes whose identifiers change when
    sockets are created during deserialization.  Falls back to matching
    by *name* when the identifier lookup fails.

    Args:
        node: The Blender node.
        identifier: Original socket identifier from serialized data.
        socket_id_map: dict mapping old identifiers -> new identifiers.
        direction: ``'INPUT'`` or ``'OUTPUT'``.
        name: Optional socket name for fallback matching.

    Returns:
        The matching ``NodeSocket`` or ``None``.
    """
    sockets = node.inputs if direction == "INPUT" else node.outputs
    resolved_id = identifier

    if node.bl_idname in ("NodeGroupOutput", "NodeGroupInput"):
        resolved_id = socket_id_map.get(identifier, identifier)
    elif node.bl_idname == "ShaderNodeGroup" and hasattr(node, "node_tree"):
        child_key = "child_" + node.node_tree.name
        if child_key in socket_id_map:
            resolved_id = socket_id_map[child_key].get(identifier, identifier)

    # Primary: match by identifier
    for sock in sockets:
        if sock.identifier == resolved_id:
            return sock

    # Fallback: match by name
    if name:
        for sock in sockets:
            if sock.name == name:
                return sock

    log.warning(
        "Socket '%s' (name='%s') not found on '%s' (%s)",
        identifier,
        name,
        node.name,
        direction,
    )
    return None


def create_interface_socket(node_tree, name, description, in_out, socket_type):
    """Create a new socket on a node group's interface.

    Args:
        node_tree: The NodeTree to add the socket to.
        name: Socket name.
        description: Socket description.
        in_out: ``'INPUT'`` or ``'OUTPUT'``.
        socket_type: Base socket type string.

    Returns:
        The newly created ``NodeTreeInterfaceSocket``.
    """
    return node_tree.interface.new_socket(
        name=name,
        description=description,
        in_out=in_out,
        socket_type=socket_type,
    )


# Type-specific deserializers


def deserialize_color_ramp(node, data):
    """Apply color ramp data to *node*."""
    ramp = node.color_ramp
    ramp.color_mode = data.get("color_mode", ramp.color_mode)
    ramp.hue_interpolation = data.get("hue_interpolation", ramp.hue_interpolation)
    ramp.interpolation = data.get("interpolation", ramp.interpolation)

    elements_data = data.get("elements", [])
    if not elements_data:
        return

    # Ensure correct number of elements
    while len(ramp.elements) < len(elements_data):
        ramp.elements.new(0.5)
    while len(ramp.elements) > len(elements_data) and len(ramp.elements) > 1:
        ramp.elements.remove(ramp.elements[-1])

    # Apply positions and colors (sorted order to avoid re-indexing)
    for i, el_data in enumerate(elements_data):
        if i < len(ramp.elements):
            ramp.elements[i].position = el_data["position"]
            ramp.elements[i].color = el_data["color"]


def deserialize_color_mapping(node, data):
    """Apply color mapping data to *node*."""
    cm = node.color_mapping
    cm.blend_color = data.get("blend_color", (0.0, 0.0, 0.0))
    cm.blend_factor = data.get("blend_factor", 0.0)
    cm.blend_type = data.get("blend_type", cm.blend_type)
    cm.brightness = data.get("brightness", 0.0)
    if "color_ramp" in data:
        deserialize_color_ramp(cm, data["color_ramp"])
    cm.contrast = data.get("contrast", 0.0)
    cm.saturation = data.get("saturation", 0.0)
    cm.use_color_ramp = data.get("use_color_ramp", False)


def deserialize_texture_mapping(node, data):
    """Apply texture mapping data to *node*."""
    tm = node.texture_mapping
    tm.mapping = data.get("mapping", tm.mapping)
    tm.mapping_x = data.get("mapping_x", tm.mapping_x)
    tm.mapping_y = data.get("mapping_y", tm.mapping_y)
    tm.mapping_z = data.get("mapping_z", tm.mapping_z)
    tm.max = data.get("max", (0.0, 0.0, 0.0))
    tm.min = data.get("min", (0.0, 0.0, 0.0))
    tm.rotation = data.get("rotation", (0.0, 0.0, 0.0))
    tm.scale = data.get("scale", (1.0, 1.0, 1.0))
    tm.translation = data.get("translation", (0.0, 0.0, 0.0))
    tm.use_max = data.get("use_max", False)
    tm.use_min = data.get("use_min", False)
    tm.vector_type = data.get("vector_type", tm.vector_type)


def deserialize_curve_mapping(node, data):
    """Apply curve mapping data to *node*."""
    mapping = node.mapping
    mapping.black_level = data.get("black_level", (0.0, 0.0, 0.0))

    curves_data = data.get("curves", [])
    for i, curve_data in enumerate(curves_data):
        if i >= len(mapping.curves):
            break
        points_data = curve_data.get("points", [])
        if not points_data:
            continue

        existing_points = mapping.curves[i].points

        # Set the first and last default points
        if len(existing_points) >= 1 and len(points_data) >= 1:
            existing_points[0].location = points_data[0].get("location", (0.0, 0.0))
        if len(existing_points) >= 2 and len(points_data) >= 2:
            existing_points[-1].location = points_data[-1].get("location", (1.0, 1.0))

        # Create intermediate points
        for k in range(1, len(points_data) - 1):
            loc = points_data[k].get("location", (0.5, 0.5))
            mapping.curves[i].points.new(loc[0], loc[1])

    mapping.clip_max_x = data.get("clip_max_x", 1.0)
    mapping.clip_max_y = data.get("clip_max_y", 1.0)
    mapping.clip_min_x = data.get("clip_min_x", 0.0)
    mapping.clip_min_y = data.get("clip_min_y", 0.0)
    mapping.extend = data.get("extend", mapping.extend)
    mapping.tone = data.get("tone", mapping.tone)
    mapping.use_clip = data.get("use_clip", False)
    mapping.white_level = data.get("white_level", (1.0, 1.0, 1.0))


def deserialize_image(node, data):
    """Set image on *node* from serialized data.

    Looks up by name first.  If the image is not already loaded and a
    ``filepath`` was stored, attempts to load it from disk.
    """
    img_name = data.get("name")
    if not img_name:
        return

    image = bpy.data.images.get(img_name)
    if image:
        node.image = image
        return

    filepath = data.get("filepath", "")
    if filepath:
        try:
            image = bpy.data.images.load(filepath)
            node.image = image
            log.info("Loaded image '%s' from '%s'.", img_name, filepath)
            return
        except (RuntimeError, OSError):
            log.warning(
                "Image '%s' not found in blend file and could not be "
                "loaded from '%s'.",
                img_name,
                filepath,
            )
            return

    log.info("Image '%s' not found in blend file.", img_name)


def deserialize_text_line(text_line, data):
    """Apply text line data."""
    text_line.body = data.get("body", "")


def deserialize_text(data):
    """Recreate or find a Text data-block from serialized data."""
    filepath = data.get("filepath", "")

    # Try to find existing text by filepath
    if filepath:
        for txt in bpy.data.texts:
            if txt.filepath == filepath:
                return txt

    text = bpy.data.texts.new(name="Text")
    # Write lines
    lines_data = data.get("lines", [])
    if lines_data:
        body = "\n".join(ld.get("body", "") for ld in lines_data)
        text.write(body)
    text.filepath = filepath

    return text


# Input / Output deserializers


def deserialize_inputs(node, data, node_data, node_tree, socket_id_map):
    """Deserialize input sockets for *node*.

    For ``NodeGroupInput`` nodes, creates interface sockets on the
    parent group instead.
    """
    if isinstance(node, (bpy.types.NodeGroupInput, bpy.types.NodeGroupOutput)):
        if isinstance(node, bpy.types.NodeGroupInput):
            for inp in node_data.get("output_order", []):
                iface_socket = create_interface_socket(
                    node_tree,
                    inp["name"],
                    inp["name"] + " Input",
                    "INPUT",
                    get_node_socket_base_type(inp["type"]),
                )
                socket_id_map[inp["identifier"]] = iface_socket.identifier
                if "default" in inp and hasattr(iface_socket, "default_value"):
                    try:
                        iface_socket.default_value = inp["default"]
                    except (TypeError, AttributeError, ValueError):
                        log.debug(
                            "Could not set default for interface input '%s'",
                            inp.get("name"),
                        )
        return

    if not hasattr(node, "inputs"):
        return

    for i, value in enumerate(data):
        if (
            value is not None
            and i < len(node.inputs)
            and hasattr(node.inputs[i], "default_value")
        ):
            try:
                node.inputs[i].default_value = value
            except (TypeError, AttributeError, ValueError):
                log.debug("Could not set input %d on '%s'", i, node.name)


def deserialize_outputs(node, data, node_data, node_tree, socket_id_map):
    """Deserialize output sockets for *node*.

    For ``NodeGroupOutput`` nodes, creates interface sockets on the
    parent group instead.
    """
    if isinstance(node, (bpy.types.NodeGroupInput, bpy.types.NodeGroupOutput)):
        if isinstance(node, bpy.types.NodeGroupOutput):
            for out in node_data.get("input_order", []):
                iface_socket = create_interface_socket(
                    node_tree,
                    out["name"],
                    out["name"] + " Output",
                    "OUTPUT",
                    get_node_socket_base_type(out["type"]),
                )
                socket_id_map[out["identifier"]] = iface_socket.identifier
                if "default" in out and hasattr(iface_socket, "default_value"):
                    try:
                        iface_socket.default_value = out["default"]
                    except (TypeError, AttributeError, ValueError):
                        log.debug(
                            "Could not set default for interface output '%s'",
                            out.get("name"),
                        )
        return

    if not hasattr(node, "outputs"):
        return

    for i, value in enumerate(data):
        if (
            value is not None
            and i < len(node.outputs)
            and hasattr(node.outputs[i], "default_value")
        ):
            try:
                node.outputs[i].default_value = value
            except (TypeError, AttributeError, ValueError):
                log.debug("Could not set output %d on '%s'", i, node.name)


# Node deserializer


def deserialize_node(node_data, node_tree, socket_id_map, defer_io=False):
    """Create a new node and apply all serialized properties.

    Args:
        node_data: dict of serialized properties for one node.
        node_tree: The Blender NodeTree to add the node to.
        socket_id_map: Mutable dict tracking socket identifier remappings.
        defer_io: If True, skip socket default assignment and return
            the deferred data as a second element.

    Returns:
        The newly created ``Node`` (or ``None``), or a tuple
        ``(Node, deferred_io_dict)`` when *defer_io* is True.
    """
    node_type = node_data.get("type", "")
    if node_type == "NodeUndefined":
        return (None, {}) if defer_io else None

    new_node = node_tree.nodes.new(type=node_type)
    new_node.label = node_data.get("label", "")

    # Node tree must be created before inputs/outputs
    if "node_tree" in node_data:
        nt_data = node_data["node_tree"]
        tree_type = nt_data.get("tree_type", "ShaderNodeTree")
        new_node.node_tree = bpy.data.node_groups.new(nt_data["name"], tree_type)
        child_key = "child_" + new_node.node_tree.name
        socket_id_map[child_key] = {}
        deserialize_node_tree(new_node.node_tree, nt_data, socket_id_map[child_key])
        # Don't process node_tree again below
        node_data = {k: v for k, v in node_data.items() if k != "node_tree"}

    # Property dispatch
    _prop_handlers = {
        "color_ramp": deserialize_color_ramp,
        "color_mapping": deserialize_color_mapping,
        "texture_mapping": deserialize_texture_mapping,
        "mapping": deserialize_curve_mapping,
        "image": deserialize_image,
        "inputs": lambda n, v: deserialize_inputs(
            n, v, node_data, node_tree, socket_id_map
        ),
        "outputs": lambda n, v: deserialize_outputs(
            n, v, node_data, node_tree, socket_id_map
        ),
        "script": lambda n, v: setattr(n, "script", deserialize_text(v)),
    }

    # Set mode-changing properties first:
    # These affect what sockets the node exposes and must precede
    # any socket default value assignment.
    for prop_name in MODE_CHANGING_PROPS:
        if prop_name not in node_data or prop_name in READONLY_DESERIALIZE_PROPS:
            continue
        try:
            setattr(new_node, prop_name, node_data[prop_name])
        except (TypeError, AttributeError, KeyError) as exc:
            log.debug(
                "Cannot set mode prop '%s' on '%s': %s", prop_name, new_node.name, exc
            )

    # Set remaining (non-socket) properties:
    _deferred_io = {}

    for prop_name, prop_value in node_data.items():
        if prop_name in READONLY_DESERIALIZE_PROPS:
            continue
        if prop_name in MODE_CHANGING_PROPS:
            continue  # Already handled in phase 1

        # Defer socket default value assignment
        if prop_name in ("inputs", "outputs"):
            _deferred_io[prop_name] = prop_value
            continue

        if prop_name in _prop_handlers:
            _prop_handlers[prop_name](new_node, prop_value)
        elif prop_name in ("parent", "_paired_output"):
            # Handled at the node-tree level
            pass
        elif prop_name in ("label", "type", "name"):
            continue
        else:
            try:
                setattr(new_node, prop_name, prop_value)
            except (TypeError, AttributeError, KeyError) as exc:
                log.debug(
                    "Cannot set '%s' on '%s': %s",
                    prop_name,
                    new_node.name,
                    exc,
                )

    # Apply deferred socket defaults:
    if not defer_io:
        for prop_name, prop_value in _deferred_io.items():
            _prop_handlers[prop_name](new_node, prop_value)

    if defer_io:
        return new_node, _deferred_io
    return new_node


# Link deserializer


def deserialize_link(node_map, link_data, socket_id_map):
    """Resolve link endpoints from serialized data.

    Args:
        node_map: dict mapping original node names to deserialized Node objects.
        link_data: dict with from_node, to_node, socket identifiers, etc.
        socket_id_map: Socket identifier remapping dict.

    Returns:
        Tuple ``(output_socket, input_socket)`` or ``(None, None)`` on error.
    """
    from_node = node_map.get(link_data["from_node"])
    to_node = node_map.get(link_data["to_node"])

    if from_node is None or to_node is None:
        log.warning(
            "Link references missing node(s): %s -> %s",
            link_data["from_node"],
            link_data["to_node"],
        )
        return None, None

    if "from_socket_index" in link_data:
        idx = link_data["from_socket_index"]
        if idx < len(from_node.outputs):
            out_sock = from_node.outputs[idx]
        else:
            out_sock = get_socket_by_identifier(
                from_node,
                link_data.get("from_socket_identifier", ""),
                socket_id_map,
                "OUTPUT",
                name=link_data.get("from_socket"),
            )
    else:
        out_sock = get_socket_by_identifier(
            from_node,
            link_data.get("from_socket_identifier", ""),
            socket_id_map,
            "OUTPUT",
            name=link_data.get("from_socket"),
        )

    if "to_socket_index" in link_data:
        idx = link_data["to_socket_index"]
        if idx < len(to_node.inputs):
            in_sock = to_node.inputs[idx]
        else:
            in_sock = get_socket_by_identifier(
                to_node,
                link_data.get("to_socket_identifier", ""),
                socket_id_map,
                "INPUT",
                name=link_data.get("to_socket"),
            )
    else:
        in_sock = get_socket_by_identifier(
            to_node,
            link_data.get("to_socket_identifier", ""),
            socket_id_map,
            "INPUT",
            name=link_data.get("to_socket"),
        )

    return out_sock, in_sock


# Frame handling: topological ordering for nested frames


def _topological_sort_frames(nodes_data):
    """Return frame node names in parent-first order.

    This ensures that when we create frames, the parent frame always
    exists before any child frame.
    """
    frame_names = [
        name for name, data in nodes_data.items() if data.get("type") == "NodeFrame"
    ]

    # Build parent -> children mapping
    parent_of = {}
    for name in frame_names:
        parent_of[name] = nodes_data[name].get("parent")

    # Simple topological sort (frames are shallow, no cycles)
    ordered = []
    visited = set()

    def visit(name):
        if name in visited:
            return
        parent = parent_of.get(name)
        if parent and parent in parent_of:
            visit(parent)
        visited.add(name)
        ordered.append(name)

    for name in frame_names:
        visit(name)

    return ordered


# Node tree deserializer


def deserialize_node_tree(node_tree, data, socket_id_map):
    """Recreate a full node tree from serialized data.

    Handles:
    - Topologically sorted frame creation (parent-first)
    - Correct absolute -> relative location conversion for nested frames
    - All node types, links, and group sockets.

    Args:
        node_tree: The target Blender NodeTree.
        data: Serialized node tree dict.
        socket_id_map: Mutable dict for socket identifier remapping.
    """
    nodes_data = data.get("nodes", {})
    links_data = data.get("links", [])
    node_map = {}  # old_name -> new Node

    # Create frames in topological (parent-first) order:
    frame_order = _topological_sort_frames(nodes_data)
    frame_name_remap = {}  # old_name -> new_name

    for frame_name in frame_order:
        frame_data = nodes_data[frame_name]
        new_frame = deserialize_node(frame_data, node_tree, socket_id_map)
        if new_frame is None:
            continue

        # Attempt to keep the original name
        new_frame.name = frame_data.get("name", frame_name)
        frame_name_remap[frame_name] = new_frame.name
        node_map[frame_name] = new_frame

    # Assign parent relationships for nested frames
    for frame_name in frame_order:
        frame_data = nodes_data[frame_name]
        parent_name = frame_data.get("parent")
        if parent_name and parent_name in frame_name_remap:
            new_name = frame_name_remap[frame_name]
            parent_new_name = frame_name_remap[parent_name]
            if new_name in node_tree.nodes and parent_new_name in node_tree.nodes:
                node_tree.nodes[new_name].parent = node_tree.nodes[parent_new_name]

    # Set frame locations (use absolute location, then convert to relative)
    for frame_name in frame_order:
        frame_data = nodes_data[frame_name]
        frame_node = node_map.get(frame_name)
        if frame_node is None:
            continue
        abs_loc = frame_data.get("location_absolute")
        if abs_loc:
            _set_location_from_absolute(frame_node, abs_loc)
        else:
            # Fallback for data without location_absolute (old format)
            loc = frame_data.get("location")
            if loc:
                frame_node.location = loc

    # Create all non-frame nodes:
    non_frame_names = [name for name in nodes_data if name not in frame_name_remap]

    # Update parent references to new frame names
    for name in non_frame_names:
        nd = nodes_data[name]
        if "parent" in nd and nd["parent"] in frame_name_remap:
            nd["parent"] = frame_name_remap[nd["parent"]]

    # Track deferred I/O for paired zone nodes
    _deferred_io_map = {}  # node_name -> deferred_io dict

    for node_name in non_frame_names:
        nd = nodes_data[node_name]
        is_paired = nd.get("type") in PAIRED_NODE_TYPES

        if is_paired:
            result = deserialize_node(nd, node_tree, socket_id_map, defer_io=True)
            new_node, deferred_io = result
            if new_node is not None:
                _deferred_io_map[node_name] = deferred_io
        else:
            new_node = deserialize_node(nd, node_tree, socket_id_map)

        if new_node is None:
            continue
        node_map[node_name] = new_node

        # Assign parent frame
        parent_name = nd.get("parent")
        if parent_name and parent_name in node_tree.nodes:
            new_node.parent = node_tree.nodes[parent_name]

        # Set location from absolute coordinates
        abs_loc = nd.get("location_absolute")
        if abs_loc:
            _set_location_from_absolute(new_node, abs_loc)
        else:
            # Fallback for old format: location is relative
            loc = nd.get("location")
            if loc is not None:
                if new_node.parent:
                    pass
                else:
                    new_node.location = loc

    # Pair zone nodes (repeat, simulation, etc.):
    for node_name in non_frame_names:
        nd = nodes_data[node_name]
        paired_name = nd.get("_paired_output")
        if paired_name and node_name in node_map and paired_name in node_map:
            input_node = node_map[node_name]
            output_node = node_map[paired_name]
            if hasattr(input_node, "pair_with_output"):
                try:
                    input_node.pair_with_output(output_node)
                except (RuntimeError, AttributeError) as exc:
                    log.warning(
                        "Failed to pair '%s' with '%s': %s",
                        node_name,
                        paired_name,
                        exc,
                    )

    # Apply deferred I/O for paired nodes:
    for node_name, deferred_io in _deferred_io_map.items():
        node = node_map.get(node_name)
        if node is None:
            continue
        nd = nodes_data[node_name]
        for prop_name, prop_value in deferred_io.items():
            if prop_name == "inputs":
                deserialize_inputs(node, prop_value, nd, node_tree, socket_id_map)
            elif prop_name == "outputs":
                deserialize_outputs(node, prop_value, nd, node_tree, socket_id_map)

    # Create links:
    links_created = 0
    for link_data in links_data:
        out_sock, in_sock = deserialize_link(node_map, link_data, socket_id_map)
        if in_sock and out_sock:
            try:
                # Blender API: links.new(input_socket, output_socket)
                node_tree.links.new(in_sock, out_sock)
                links_created += 1
            except (RuntimeError, TypeError, ValueError) as exc:
                log.warning(
                    "Failed to create link %s.%s -> %s.%s: %s",
                    link_data.get("from_node"),
                    link_data.get("from_socket"),
                    link_data.get("to_node"),
                    link_data.get("to_socket"),
                    exc,
                )
        else:
            log.warning(
                "Could not resolve link endpoints: %s.%s -> %s.%s"
                " (out_sock=%s, in_sock=%s)",
                link_data.get("from_node"),
                link_data.get("from_socket"),
                link_data.get("to_node"),
                link_data.get("to_socket"),
                out_sock,
                in_sock,
            )
    log.debug("Created %d/%d links", links_created, len(links_data))


def _set_location_from_absolute(node, abs_loc):
    """Set a node's location from absolute canvas coordinates.

    In Blender, ``node.location`` is relative to ``node.parent``.
    So we need to subtract the parent's absolute location to get
    the correct relative location.
    """
    if node.parent:
        parent_abs = node.parent.location_absolute
        node.location = (
            abs_loc[0] - parent_abs[0],
            abs_loc[1] - parent_abs[1],
        )
    else:
        node.location = abs_loc
