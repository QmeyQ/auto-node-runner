"""
Node Runner - Import & export shader nodes as shareable strings.

Serializes Blender shader node trees to compressed, base64-encoded
strings that can be shared via text, comments, or documentation.
"""

bl_info = {
    "name": "Node Runner",
    "description": "Import and export nodes as strings",
    "author": "Noah Thiering <noah.thiering@gmail.com>",
    "version": (1, 4, 1),
    "blender": (4, 5, 0),
    "category": "Node",
}


def register():
    """Register all operators and menu entries."""
    from . import operators, node_data

    node_data.refresh()
    operators.register()


def unregister():
    """Unregister all operators and menu entries."""
    from . import operators

    operators.unregister()
