"""
Helper factories for building mock nodes, node trees, etc. in tests.

The bpy / mathutils mocks are installed by the root-level ``conftest.py``
which runs before this file is loaded.
"""

from unittest.mock import MagicMock
from mathutils import Vector as MockVector


# Helper factories


class MockRNAProperty:
    """Minimal stand-in for ``bpy.types.Property``."""

    def __init__(self, identifier, is_readonly=False):
        self.identifier = identifier
        self.is_readonly = is_readonly


class MockSocket:
    """Mock for a NodeSocket with identifier, name, and default_value."""

    def __init__(self, name, identifier=None, bl_idname="NodeSocketFloat"):
        self.name = name
        self.identifier = identifier or name
        self.bl_idname = bl_idname
        self.default_value = 0.0


class MockNode:
    """A configurable mock node for testing serialization."""

    def __init__(
        self,
        name="TestNode",
        bl_idname="ShaderNodeMath",
        label="",
        location=(0, 0),
        parent=None,
        **props,
    ):
        self.name = name
        self.bl_idname = bl_idname
        self.label = label
        self.location = MockVector(location)
        self.location_absolute = MockVector(location)
        self.parent = parent
        self._extra = props
        for k, v in props.items():
            setattr(self, k, v)

        # Build a mock bl_rna
        self.bl_rna = MagicMock()
        rna_props = [
            MockRNAProperty("name"),
            MockRNAProperty("location"),
            MockRNAProperty("location_absolute", is_readonly=True),
        ]
        for k in props:
            rna_props.append(MockRNAProperty(k))
        if parent is not None:
            rna_props.append(MockRNAProperty("parent"))
        self.bl_rna.properties = rna_props


class MockNodeTree:
    """A configurable mock node tree."""

    def __init__(self, name="NodeTree", bl_idname="ShaderNodeTree"):
        self.name = name
        self.bl_idname = bl_idname
        self.nodes = MockNodeCollection()
        self.links = MockLinkCollection()
        self.interface = MagicMock()


class MockNodeCollection:
    """Mock for NodeTree.nodes."""

    def __init__(self):
        self._nodes = {}

    def __contains__(self, name):
        return name in self._nodes

    def __getitem__(self, name):
        return self._nodes[name]

    def __iter__(self):
        return iter(self._nodes.values())

    def __len__(self):
        return len(self._nodes)

    def new(self, type=""):
        node = MagicMock()
        node.bl_idname = type
        node.name = f"Node_{len(self._nodes)}"
        node.label = ""
        node.parent = None
        node.location = MockVector([0, 0])
        node.location_absolute = MockVector([0, 0])
        # Add default sockets so link tests work
        node.inputs = [MockSocket("Value", "Value")]
        node.outputs = [MockSocket("Value", "Value")]
        self._nodes[node.name] = node
        return node

    def add(self, node):
        self._nodes[node.name] = node


class MockLinkCollection(list):
    """Mock for NodeTree.links - matches Blender 5.x API.

    ``links.new(input_socket, output_socket)``
    """

    def new(self, input_socket, output_socket, verify_limits=True):
        link = MagicMock()
        link.from_socket = output_socket  # source / output
        link.to_socket = input_socket  # destination / input
        # Store node references for serialization tests
        link.from_node = getattr(output_socket, "_node", MagicMock())
        link.to_node = getattr(input_socket, "_node", MagicMock())
        self.append(link)
        return link
