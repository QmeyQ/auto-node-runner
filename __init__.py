"""
Node Runner - Import & export shader nodes as shareable strings.

Serializes Blender shader node trees to compressed, base64-encoded
strings that can be shared via text, comments, or documentation.

Copyright (C) 2024 Noah Thiering
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Modifications (2026-06-09) by Auto Texture contributors:
  - Renamed from "Node Runner" to "Auto Node Runner" for addon isolation
  - Added i18n and panel module registration in register()
  - Added fault-tolerant unregister() with history.cleanup_nodetmp()
  - All internal package references use __package__ for addon isolation
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
