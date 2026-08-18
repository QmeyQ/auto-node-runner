"""
Node AI module for Auto Node Runner.

Reads the current shader node tree and collects node properties,
input/output sockets, and links into a structured dict for AI processing.

Copyright (C) 2026 Auto Texture contributors
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import json
import mathutils

_SKIP_PROPS = frozenset({
    "rna_type", "type", "bl_rna", "bl_idname", "bl_label",
    "bl_description", "bl_icon", "bl_static_type",
    "dimensions", "inputs", "outputs", "internal_links",
    "parent", "users", "tag", "use_tag",
    "select", "active", "show_preview", "show_options",
    "show_texture", "show_label", "width", "height",
    "width_hidden", "location", "name",
    "socket_value_update", "draw_buttons", "draw_buttons_ext",
    "draw_label", "poll", "poll_instance", "update",
    "input_template", "output_template",
    "is_registered_node_type", "is_evaluated",
    "is_in_edit", "is_modified", "is_valid",
    "bl_width_default", "bl_width_min", "bl_width_max",
    "bl_height_default", "bl_height_min", "bl_height_max",
    "cache_point_density", "calc_point_density",
    "calc_point_density_minmax",
    "debug_zone_body_lazy_function_graph",
    "debug_zone_lazy_function_graph",
    "location_absolute", "warning_propagation",
    "hide",
    "color_tag", "color",
})


def _socket_value(socket):
    """Extract a serializable value from a node socket."""
    try:
        v = socket.default_value
    except AttributeError:
        v = None
    if v is None:
        try:
            v = socket.default_value
        except AttributeError:
            return None
    if isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, mathutils.Vector):
        return list(v)
    if isinstance(v, mathutils.Color):
        return [v.r, v.g, v.b]
    if hasattr(v, "__len__") and not isinstance(v, str):
        try:
            return list(v)
        except Exception:
            return str(v)
    return str(v)


def _node_props(node):
    """Collect non-default properties of a node."""
    result = {}
    for key in node.bl_rna.properties.keys():
        if key in _SKIP_PROPS:
            continue
        try:
            prop_rna = node.bl_rna.properties.get(key)
            if prop_rna is not None and prop_rna.is_readonly:
                continue
        except Exception:
            pass
        try:
            val = getattr(node, key)
        except AttributeError:
            continue
        if isinstance(val, (int, float, str, bool)):
            result[key] = val
        elif isinstance(val, mathutils.Vector):
            result[key] = list(val)
        elif isinstance(val, mathutils.Color):
            result[key] = [val.r, val.g, val.b]
        elif hasattr(val, "name") and hasattr(val, "bl_rna"):
            result[key] = val.name
        else:
            pass
    return result


def _node_inputs(node):
    """Collect input socket info."""
    inputs = {}
    for sock in node.inputs:
        info = {"name": sock.name, "type": sock.type}
        if sock.is_linked:
            info["linked"] = True
        else:
            info["value"] = _socket_value(sock)
        inputs[sock.name] = info
    return inputs


def _node_outputs(node):
    """Collect output socket info."""
    outputs = {}
    for sock in node.outputs:
        info = {"name": sock.name, "type": sock.type}
        if sock.is_linked:
            info["linked"] = True
        outputs[sock.name] = info
    return outputs


def collect_node_tree(context):
    """Read the current node editor's node tree and return a structured dict.

    Returns dict with:
      "nodes": {node_name: {type, label, props, inputs, outputs}}
      "links": [{from_node, from_socket, to_node, to_socket}, ...]
    Returns None if no valid node tree is found.
    """
    node_tree = None
    if context.space_data and hasattr(context.space_data, "node_tree"):
        node_tree = context.space_data.node_tree
    if node_tree is None:
        mat = getattr(context, "material", None)
        if mat and mat.node_tree:
            node_tree = mat.node_tree
    if node_tree is None:
        return None

    nodes_info = {}
    for node in node_tree.nodes:
        nid = f"{node.name}"
        nodes_info[nid] = {
            "type": node.bl_idname,
            "label": node.label or "",
            "props": _node_props(node),
            "inputs": _node_inputs(node),
            "outputs": _node_outputs(node),
        }

    links_info = []
    for link in node_tree.links:
        links_info.append({
            "from_node": link.from_node.name,
            "from_socket": link.from_socket.name,
            "to_node": link.to_node.name,
            "to_socket": link.to_socket.name,
        })

    return {"nodes": nodes_info, "links": links_info}


def collect_and_format(context):
    """Collect node tree info and return a JSON string for display.

    Returns empty string if no node tree found.
    """
    data = collect_node_tree(context)
    if data is None:
        return ""
    return json.dumps(data, ensure_ascii=False, indent=2)


# ---------- AI 系统提示词 ----------

NODE_AI_SYSTEM_PROMPT = (
    "你是一个 Blender 节点树编辑助手。用户会提供当前节点树的结构信息（JSON），"
    "你需要根据用户的指令修改节点属性和输入值。\n\n"
    "节点树结构说明：\n"
    "- nodes: 每个节点的名称（JSON 的 key）、类型(type)、属性(props)、输入(inputs)、输出(outputs)\n"
    "- links: 节点之间的连接关系\n"
    "- inputs 中未连接的 socket 有 value 字段，已连接的有 linked: true\n\n"
    "输出格式：返回一个 JSON 对象，包含要修改的节点信息：\n"
    '{\n'
    '  "nodes": {\n'
    '    "节点名称": {\n'
    '      "props": {"属性名": 新值},\n'
    '      "inputs": {"输入socket名": 新值}\n'
    '    }\n'
    '  },\n'
    '  "links": [\n'
    '    {"from": "源节点名", "from_socket": "源输出socket名", "to": "目标节点名", "to_socket": "目标输入socket名"}\n'
    '  ]\n'
    '}\n\n'
    "规则：\n"
    "节点名称必须与输入 JSON 中 nodes 的 key 完全一致（包括 .001 等后缀）不得增删空格等\n"
    "属性名必须与输入 JSON 中 props 的 key 完全一致\n"
    "只包含需要修改的节点，不要包含未修改的节点\n"
    "如果需要连接两个节点，使用 links 数组指定连接关系\n"
    "如果目标节点不存在，可以直接在 nodes 中指定其属性，系统会自动新建\n"
    "inputs 中如果该 socket 需要连接到其他节点的输出，可以用 {\"link\": \"源节点名.源输出socket名\"} 格式\n"
    )


def extract_json_from_response(text):
    """从 AI 回复文本中提取 JSON 对象。"""
    import re
    md_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if md_match:
        try:
            return json.loads(md_match.group(1))
        except json.JSONDecodeError:
            pass
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _set_socket_value(socket, value):
    """Set a socket's default value from a serializable form."""
    if socket.is_linked:
        return False
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    try:
        dv = socket.default_value
    except AttributeError:
        return False
    if isinstance(dv, (int, float, str, bool)):
        try:
            socket.default_value = value
            return True
        except Exception:
            return False
    if isinstance(value, list):
        try:
            for i, v in enumerate(value):
                if i < len(dv):
                    dv[i] = v
            return True
        except Exception:
            return False
    try:
        socket.default_value = value
        return True
    except Exception:
        return False


def _set_node_prop(node, key, value):
    """Set a node property from a serializable value."""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    try:
        prop_rna = node.bl_rna.properties.get(key)
        if prop_rna is not None and prop_rna.is_readonly:
            return False
    except Exception:
        pass
    if key == "image" and isinstance(value, str):
        import bpy
        img = bpy.data.images.get(value)
        if img is None:
            return False
        try:
            setattr(node, key, img)
            return True
        except Exception:
            return False
    try:
        attr = getattr(node, key)
    except AttributeError:
        return False
    if isinstance(attr, (int, float, str, bool)):
        try:
            setattr(node, key, value)
            return True
        except Exception:
            return False
    if isinstance(value, list):
        try:
            for i, v in enumerate(value):
                if i < len(attr):
                    attr[i] = v
            return True
        except Exception:
            return False
    try:
        setattr(node, key, value)
        return True
    except Exception:
        return False


def _unwrap_value(v):
    """If v is a dict with 'value' key, return v['value']; else return v."""
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def _normalize_name(name):
    """Normalize node name by removing spaces, underscores, hyphens and lowercasing."""
    import re
    return re.sub(r"[\s_\-.]", "", name).lower()


def _find_node(node_tree, name):
    """Find node by exact name, then try normalized matching."""
    node = node_tree.nodes.get(name)
    if node:
        return node
    target = _normalize_name(name)
    if not target:
        return None
    for n in node_tree.nodes:
        if _normalize_name(n.name) == target:
            return n
    return None


_NODE_TYPE_MAP = {
    "Texture Coordinate": "ShaderNodeTexCoord",
    "Mapping": "ShaderNodeMapping",
    "Image Texture": "ShaderNodeTexImage",
    "Displacement": "ShaderNodeDisplacement",
    "Noise Texture": "ShaderNodeTexNoise",
    "Color Ramp": "ShaderNodeValToRGB",
    "ColorRamp": "ShaderNodeValToRGB",
    "Glass BSDF": "ShaderNodeBsdfGlass",
    "Principled BSDF": "ShaderNodeBsdfPrincipled",
    "Diffuse BSDF": "ShaderNodeBsdfDiffuse",
    "Glossy BSDF": "ShaderNodeBsdfGlossy",
    "Translucent BSDF": "ShaderNodeBsdfTranslucent",
    "Transparent BSDF": "ShaderNodeBsdfTransparent",
    "Velvet BSDF": "ShaderNodeBsdfVelvet",
    "Subsurface Scattering": "ShaderNodeSubsurfaceScattering",
    "Emission": "ShaderNodeEmission",
    "Holdout": "ShaderNodeHoldout",
    "Add Shader": "ShaderNodeAddShader",
    "Mix Shader": "ShaderNodeMixShader",
    "Volume Absorption": "ShaderNodeVolumeAbsorption",
    "Volume Scatter": "ShaderNodeVolumeScatter",
    "Volume Info": "ShaderNodeVolumeInfo",
    "Add Volume": "ShaderNodeAddShader",
    "Material Output": "ShaderNodeOutputMaterial",
    "Bump": "ShaderNodeBump",
    "Normal Map": "ShaderNodeNormalMap",
    "Value": "ShaderNodeValue",
    "RGB": "ShaderNodeRGB",
    "Math": "ShaderNodeMath",
    "Vector Math": "ShaderNodeVectorMath",
    "Color Mix": "ShaderNodeMixRGB",
    "Mix": "ShaderNodeMix",
    "Mix Color": "ShaderNodeMix",
    "Curve RGB": "ShaderNodeCurveRGB",
    "Hue/Saturation": "ShaderNodeHueSaturation",
    "Hue/Saturation/Value": "ShaderNodeHueSaturation",
    "Bright/Contrast": "ShaderNodeBrightContrast",
    "Gamma": "ShaderNodeGamma",
    "Invert": "ShaderNodeInvert",
    "Light Path": "ShaderNodeLightPath",
    "Fresnel": "ShaderNodeFresnel",
    "Layer Weight": "ShaderNodeLayerWeight",
    "Attribute": "ShaderNodeAttribute",
    "Camera Data": "ShaderNodeCamera",
    "Object Info": "ShaderNodeObjectInfo",
    "Particle Info": "ShaderNodeParticleInfo",
    "Vertex Color": "ShaderNodeVertexColor",
    "Geometry": "ShaderNodeNewGeometry",
    "UV Map": "ShaderNodeUVMap",
    "Wireframe": "ShaderNodeWireframe",
    "Wavelength": "ShaderNodeWavelength",
    "Blackbody": "ShaderNodeBlackbody",
    "Combine Color": "ShaderNodeCombineColor",
    "Separate Color": "ShaderNodeSeparateColor",
    "Combine XYZ": "ShaderNodeCombineXYZ",
    "Separate XYZ": "ShaderNodeSeparateXYZ",
    "Group": "ShaderNodeGroup",
    "Reroute": "NodeReroute",
    "Ambient Occlusion": "ShaderNodeAmbientOcclusion",
    "Bevel": "ShaderNodeBevel",
    "Shader to RGB": "ShaderNodeShaderToRGB",
    "Vector Rotate": "ShaderNodeVectorRotate",
    "Vector Curve": "ShaderNodeVectorCurve",
    "Curve Time": "ShaderNodeCurveTime",
    "Pixelate": "ShaderNodePixelate",
    "Tex Coord": "ShaderNodeTexCoord",
}


def _get_node_type(name):
    """根据节点名推测 Blender 节点类型。"""
    if name in _NODE_TYPE_MAP:
        return _NODE_TYPE_MAP[name]
    for sep in (" (", "(", "."):
        if sep in name:
            base = name.rsplit(sep, 1)[0]
            if base in _NODE_TYPE_MAP:
                return _NODE_TYPE_MAP[base]
    norm = _normalize_name(name)
    for key, val in _NODE_TYPE_MAP.items():
        if _normalize_name(key) == norm:
            return val
    return None


def _create_node(node_tree, name):
    """根据节点名新建节点，返回节点或 None。"""
    node_type = _get_node_type(name)
    if node_type is None:
        return None
    try:
        node = node_tree.nodes.new(node_type)
        if node.name != name:
            node.name = name
        node.label = name
        return node
    except Exception:
        return None


def apply_node_changes(node_tree, changes):
    """Apply JSON node changes to the node tree.

    changes: dict with "nodes" key mapping node names to {props, inputs},
             and optional "links" list for node connections.
    Returns list of applied change descriptions for logging.
    """
    if not isinstance(changes, dict):
        return ["Invalid changes format"]
    nodes_changes = changes.get("nodes", {})
    if not isinstance(nodes_changes, dict):
        return ["Invalid nodes format"]
    applied = []
    for node_name, node_data in nodes_changes.items():
        if not isinstance(node_data, dict):
            continue
        node = _find_node(node_tree, node_name)
        if node is None:
            node = _create_node(node_tree, node_name)
        if node is None:
            applied.append(f"  节点不存在且无法新建: {node_name}")
            continue
        actual_name = node.name
        props = node_data.get("props", {})
        if isinstance(props, dict):
            for pk, pv in props.items():
                pv = _unwrap_value(pv)
                if _set_node_prop(node, pk, pv):
                    applied.append(f"  {actual_name}.{pk} = {pv}")
                else:
                    applied.append(f"  设置失败: {actual_name}.{pk}")
        inputs = node_data.get("inputs", {})
        if isinstance(inputs, dict):
            for ik, iv in inputs.items():
                iv = _unwrap_value(iv)
                if isinstance(iv, dict) and "link" in iv:
                    link_spec = iv["link"]
                    if isinstance(link_spec, str) and "." in link_spec:
                        src_name, src_sock = link_spec.rsplit(".", 1)
                        src_node = _find_node(node_tree, src_name)
                        if src_node is None:
                            src_node = _create_node(node_tree, src_name)
                        if src_node is not None:
                            from_sock = src_node.outputs.get(src_sock)
                            to_sock = node.inputs.get(ik)
                            if from_sock and to_sock:
                                node_tree.links.new(from_sock, to_sock)
                                applied.append(f"  连接: {src_name}.{src_sock} -> {actual_name}.{ik}")
                            else:
                                applied.append(f"  连接失败: {link_spec} -> {actual_name}.{ik}")
                        else:
                            applied.append(f"  连接失败: 源节点不存在 {src_name}")
                    continue
                sock = node.inputs.get(ik)
                if sock is None:
                    applied.append(f"  输入不存在: {actual_name}.{ik}")
                    continue
                if sock.is_linked:
                    continue
                if _set_socket_value(sock, iv):
                    applied.append(f"  {actual_name}.inputs[{ik}] = {iv}")
                else:
                    applied.append(f"  设置失败: {actual_name}.inputs[{ik}]")
    links_changes = changes.get("links", [])
    if isinstance(links_changes, list):
        for link in links_changes:
            if not isinstance(link, dict):
                continue
            src_name = link.get("from", link.get("from_node", ""))
            dst_name = link.get("to", link.get("to_node", ""))
            src_sock_name = link.get("from_socket", "")
            dst_sock_name = link.get("to_socket", "")
            src_node = _find_node(node_tree, src_name)
            if src_node is None:
                src_node = _create_node(node_tree, src_name)
            dst_node = _find_node(node_tree, dst_name)
            if dst_node is None:
                dst_node = _create_node(node_tree, dst_name)
            if src_node is None or dst_node is None:
                applied.append(f"  连接失败: {src_name} -> {dst_name}")
                continue
            from_sock = src_node.outputs.get(src_sock_name)
            to_sock = dst_node.inputs.get(dst_sock_name)
            if from_sock is None or to_sock is None:
                applied.append(f"  连接失败: {src_name}.{src_sock_name} -> {dst_name}.{dst_sock_name}")
                continue
            node_tree.links.new(from_sock, to_sock)
            applied.append(f"  连接: {src_name}.{src_sock_name} -> {dst_name}.{dst_sock_name}")
    return applied


def estimate_tokens(text):
    """Rough token estimation for mixed Chinese/English text."""
    if not text:
        return 0
    return len(text) // 3


def trim_history(history, max_tokens=3000):
    """Trim conversation history to fit within token limit.

    history: list of {role, content} messages.
    Returns trimmed history (oldest messages removed first).
    """
    total = sum(estimate_tokens(m.get("content", "")) for m in history)
    while total > max_tokens and history:
        removed = history.pop(0)
        total -= estimate_tokens(removed.get("content", ""))
    return history