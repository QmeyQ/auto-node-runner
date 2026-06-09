"""
Root-level pytest conftest - installs ``bpy`` and ``mathutils`` mocks
into ``sys.modules`` *before* any ``node_runner`` code is imported.

Because this file lives **outside** the ``node_runner`` package, pytest
processes it before discovering or importing anything from the package.
"""

import sys
import types
from unittest.mock import MagicMock


# mathutils mocks


class MockVector(list):
    """Minimal mathutils.Vector substitute."""

    def __init__(self, *args):
        if len(args) == 1 and hasattr(args[0], "__iter__"):
            super().__init__(args[0])
        else:
            super().__init__(args)

    def __add__(self, other):
        return MockVector([a + b for a, b in zip(self, other)])

    def __sub__(self, other):
        return MockVector([a - b for a, b in zip(self, other)])

    @property
    def x(self):
        return self[0]

    @property
    def y(self):
        return self[1]


class MockColor(list):
    """Minimal mathutils.Color substitute."""

    def __init__(self, *args):
        if len(args) == 1 and hasattr(args[0], "__iter__"):
            super().__init__(args[0])
        else:
            super().__init__(args)


class MockEuler(list):
    """Minimal mathutils.Euler substitute."""

    def __init__(self, *args):
        if len(args) == 1 and hasattr(args[0], "__iter__"):
            super().__init__(args[0])
        else:
            super().__init__(args)


mathutils_mod = types.ModuleType("mathutils")
mathutils_mod.Vector = MockVector
mathutils_mod.Color = MockColor
mathutils_mod.Euler = MockEuler
sys.modules["mathutils"] = mathutils_mod


# bpy.types stub classes


class _TypeBase:
    pass


class ColorRamp(_TypeBase):
    pass


class ShaderNodeTree(_TypeBase):
    pass


class GeometryNodeTree(_TypeBase):
    pass


class ColorMapping(_TypeBase):
    pass


class TexMapping(_TypeBase):
    pass


class CurveMapping(_TypeBase):
    pass


class CurveMap(_TypeBase):
    pass


class CurveMapPoint(_TypeBase):
    pass


class Image(_TypeBase):
    name = ""


class ImageUser(_TypeBase):
    pass


class NodeFrame(_TypeBase):
    pass


class Text(_TypeBase):
    pass


class TextLine(_TypeBase):
    pass


class Object(_TypeBase):
    pass


class NodeSocketStandard(_TypeBase):
    pass


class NodeGroupInput(_TypeBase):
    pass


class NodeGroupOutput(_TypeBase):
    pass


class NodeSocketVirtual(_TypeBase):
    pass


class NodeTreeInterfaceSocket(_TypeBase):
    identifier = ""


class bpy_prop_collection(list):
    def values(self):
        return list(self)


class bpy_prop_array(list):
    pass


class Operator(_TypeBase):
    bl_idname = ""
    bl_label = ""
    bl_options = set()


class AddonPreferences(_TypeBase):
    bl_idname = ""


class Menu(_TypeBase):
    bl_idname = ""
    bl_label = ""


class NodeLink(_TypeBase):
    pass


class NodeTree(_TypeBase):
    pass


class NODE_MT_context_menu:
    _entries: list = []

    @classmethod
    def append(cls, func):
        cls._entries.append(func)

    @classmethod
    def remove(cls, func):
        cls._entries.remove(func)


# Assemble bpy module

bpy_mod = types.ModuleType("bpy")

bpy_types = types.ModuleType("bpy.types")
for _name, _cls in [
    ("ColorRamp", ColorRamp),
    ("ShaderNodeTree", ShaderNodeTree),
    ("GeometryNodeTree", GeometryNodeTree),
    ("ColorMapping", ColorMapping),
    ("TexMapping", TexMapping),
    ("CurveMapping", CurveMapping),
    ("CurveMap", CurveMap),
    ("CurveMapPoint", CurveMapPoint),
    ("Image", Image),
    ("ImageUser", ImageUser),
    ("NodeFrame", NodeFrame),
    ("Text", Text),
    ("TextLine", TextLine),
    ("Object", Object),
    ("NodeSocketStandard", NodeSocketStandard),
    ("NodeGroupInput", NodeGroupInput),
    ("NodeGroupOutput", NodeGroupOutput),
    ("NodeSocketVirtual", NodeSocketVirtual),
    ("NodeTreeInterfaceSocket", NodeTreeInterfaceSocket),
    ("bpy_prop_collection", bpy_prop_collection),
    ("bpy_prop_array", bpy_prop_array),
    ("Operator", Operator),
    ("AddonPreferences", AddonPreferences),
    ("Menu", Menu),
    ("NodeLink", NodeLink),
    ("NodeTree", NodeTree),
    ("NODE_MT_context_menu", NODE_MT_context_menu),
]:
    setattr(bpy_types, _name, _cls)

bpy_mod.types = bpy_types

bpy_props = types.ModuleType("bpy.props")
bpy_props.StringProperty = lambda **kw: ""
bpy_props.BoolProperty = lambda **kw: False
bpy_props.EnumProperty = lambda **kw: ""
bpy_mod.props = bpy_props

bpy_mod.data = MagicMock()
bpy_mod.context = MagicMock()

bpy_path = types.ModuleType("bpy.path")
bpy_path.abspath = lambda p: p  # identity in tests
bpy_mod.path = bpy_path

bpy_utils = types.ModuleType("bpy.utils")
bpy_utils.register_class = lambda cls: None
bpy_utils.unregister_class = lambda cls: None
bpy_mod.utils = bpy_utils

bpy_mod.ops = MagicMock()

sys.modules["bpy"] = bpy_mod
sys.modules["bpy.types"] = bpy_types
sys.modules["bpy.props"] = bpy_props
sys.modules["bpy.utils"] = bpy_utils
