"""
Node Runner - Import & export shader nodes as shareable strings.

Serializes Blender shader node trees to compressed, base64-encoded
strings that can be shared via text, comments, or documentation.

Modifications (2026-06-08):
  - Renamed from "Node Runner" to "Auto Node Runner" to avoid conflict
    with the original addon when both are installed simultaneously
  - All internal package references use __package__ for addon isolation
"""

bl_info = {
    "name": "Auto Node Runner",
    "description": "Import and export nodes as strings. Includes Auto Texture panel for automatic texture matching.",
    "author": "Noah Thiering <noah.thiering@gmail.com>",
    "version": (0, 0, 1),
    "blender": (4, 5, 0),
    "category": "Node",
}


def register():
    """Register all operators, panels, and properties.
    修改于 2026-06-08：注册自动贴图相关的属性和类"""
    from . import operators, node_data

    node_data.refresh()
    operators.register()


def unregister():
    """Unregister all operators, panels, and properties.
    修改于 2026-06-08：注销自动贴图相关的属性和类"""
    from . import operators

    operators.unregister()
