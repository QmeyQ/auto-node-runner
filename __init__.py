"""
Node Runner - Import & export shader nodes as shareable strings.

Serializes Blender shader node trees to compressed, base64-encoded
strings that can be shared via text, comments, or documentation.
"""

import logging

log = logging.getLogger(__name__)

bl_info = {
    "name": "Auto Node Runner",
    "description": "Import and export nodes as strings",
    "author": "Noah Thiering <noah.thiering@gmail.com>",
    "version": (0, 0, 1),
    "blender": (4, 5, 0),
    "category": "Node",
}


def register():
    """Register all operators and menu entries."""
    # [Auto Texture] Register in order: i18n -> operators -> panel - Modified: 2026-06-09
    from . import operators, node_data, i18n, panel

    node_data.refresh()
    i18n.register()
    operators.register()
    panel.register()


def unregister():
    """Unregister all operators and menu entries, with full cleanup."""
    # [Auto Texture] Unregister with fault-tolerant cleanup and nodetmp removal - Modified: 2026-06-09
    from . import operators, i18n, panel, history

    try:
        panel.unregister()
    except Exception as e:
        log.warning(f"Panel unregister failed: {e}")

    try:
        operators.unregister()
    except Exception as e:
        log.warning(f"Operators unregister failed: {e}")

    try:
        history.cleanup_nodetmp()
    except Exception as e:
        log.warning(f"Cleanup nodetmp failed: {e}")

    try:
        i18n.unregister()
    except Exception as e:
        log.warning(f"i18n unregister failed: {e}")
