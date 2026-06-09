"""Extra encoding tests: export flow, strip helpers, values_equal, delta."""

import json
import xml.etree.ElementTree as ET

import pytest

from node_runner.encoding import (
    encode,
    decode,
    encode_ai_json,
    decode_ai_json,
    encode_as,
    decode_as,
    detect_format,
    FORMAT_HASH,
    FORMAT_JSON,
    FORMAT_AI_JSON,
    FORMAT_XML,
    _values_equal,
    _compact_data,
)
from node_runner.node_data import NODE_DEFAULTS
from node_runner.constants import EXPORT_HEADER
from node_runner.operators import _strip_image_paths

_SIMPLE_DATA = {"nodes": {}, "links": [], "name": "Test"}

_NODE_DATA = {
    "nodes": {
        "Math": {
            "type": "ShaderNodeMath",
            "label": "",
            "operation": "ADD",
            "location": [100.0, 200.0],
            "location_absolute": [100.0, 200.0],
            "inputs": [0.5, 0.5],
        }
    },
    "links": [],
    "name": "Material",
}


class TestExportImportFlow:
    """End-to-end tests simulating the operator build/strip helpers."""

    FAKE_VERSION = "4.5.0"

    def _build_export_string(self, data, export_name, fmt):
        """Mirror the operator helper ``_build_export_string``."""
        data = dict(data)
        data["blender_version"] = self.FAKE_VERSION

        if fmt in (FORMAT_JSON, FORMAT_AI_JSON, FORMAT_XML):
            data["export_name"] = export_name
            return encode_as(data, fmt)
        encoded = encode_as(data, fmt)
        return (export_name or "MyNodes") + EXPORT_HEADER + encoded

    def _strip_header_and_detect(self, raw):
        """Mirror the operator helper ``_strip_header_and_detect``."""
        if EXPORT_HEADER in raw:
            payload = raw.split(EXPORT_HEADER, 1)[1]
            return FORMAT_HASH, payload
        fmt = detect_format(raw)
        return fmt, raw

    @pytest.mark.parametrize(
        "fmt", [FORMAT_HASH, FORMAT_JSON, FORMAT_AI_JSON, FORMAT_XML]
    )
    def test_roundtrip_all_formats(self, fmt):
        export_str = self._build_export_string(_NODE_DATA, "TestExport", fmt)
        detected_fmt, payload = self._strip_header_and_detect(export_str)
        if fmt == FORMAT_AI_JSON:
            assert detected_fmt == FORMAT_AI_JSON
        else:
            assert detected_fmt == fmt
        decoded = decode_as(payload, detected_fmt)
        decoded.pop("export_name", None)
        decoded.pop("blender_version", None)
        assert decoded["name"] == "Material"
        assert "Math" in decoded["nodes"]
        if fmt == FORMAT_JSON:
            assert decoded == _NODE_DATA
        if fmt in (FORMAT_HASH,):
            assert decoded["nodes"]["Math"]["operation"] == "ADD"

    def test_hash_export_has_header(self):
        export_str = self._build_export_string(_SIMPLE_DATA, "Foo", FORMAT_HASH)
        assert export_str.startswith("Foo" + EXPORT_HEADER)

    def test_json_export_is_valid_json(self):
        export_str = self._build_export_string(_SIMPLE_DATA, "Foo", FORMAT_JSON)
        parsed = json.loads(export_str)
        assert parsed["export_name"] == "Foo"

    def test_xml_export_is_valid_xml(self):
        export_str = self._build_export_string(_SIMPLE_DATA, "Foo", FORMAT_XML)
        root = ET.fromstring(export_str)
        assert root.tag == "node_runner"

    def test_json_no_header_prefix(self):
        export_str = self._build_export_string(_SIMPLE_DATA, "Foo", FORMAT_JSON)
        assert not export_str.startswith("Foo" + EXPORT_HEADER)
        assert export_str.lstrip().startswith("{")

    def test_xml_no_header_prefix(self):
        export_str = self._build_export_string(_SIMPLE_DATA, "Foo", FORMAT_XML)
        assert not export_str.startswith("Foo" + EXPORT_HEADER)
        assert export_str.lstrip().startswith("<")

    @pytest.mark.parametrize(
        "fmt", [FORMAT_HASH, FORMAT_JSON, FORMAT_AI_JSON, FORMAT_XML]
    )
    def test_blender_version_embedded(self, fmt):
        export_str = self._build_export_string(_SIMPLE_DATA, "V", fmt)
        _, payload = self._strip_header_and_detect(export_str)
        decoded = decode_as(payload, fmt)
        assert decoded["blender_version"] == self.FAKE_VERSION

    def test_no_version_in_legacy_data(self):
        """Data without blender_version should decode cleanly."""
        encoded = encode(_SIMPLE_DATA)
        decoded = decode(encoded)
        assert "blender_version" not in decoded


class TestStripImagePaths:
    """Test _strip_image_paths helper."""

    def test_removes_filepath_from_image_data(self):
        data = {
            "nodes": {
                "TexNode": {
                    "type": "ShaderNodeTexImage",
                    "image": {"name": "tex.png", "filepath": "/path/tex.png"},
                },
            },
            "links": [],
        }
        _strip_image_paths(data)
        assert "filepath" not in data["nodes"]["TexNode"]["image"]
        assert data["nodes"]["TexNode"]["image"]["name"] == "tex.png"

    def test_no_op_when_no_image(self):
        data = {
            "nodes": {
                "Math": {"type": "ShaderNodeMath", "operation": "ADD"},
            },
            "links": [],
        }
        _strip_image_paths(data)
        assert data["nodes"]["Math"]["operation"] == "ADD"

    def test_no_op_when_image_has_no_filepath(self):
        data = {
            "nodes": {
                "TexNode": {
                    "image": {"name": "tex.png"},
                },
            },
        }
        _strip_image_paths(data)
        assert data["nodes"]["TexNode"]["image"] == {"name": "tex.png"}

    def test_handles_empty_nodes(self):
        data = {"nodes": {}}
        _strip_image_paths(data)  # should not raise


class TestValuesEqual:
    """Tests for the _values_equal helper used in delta encoding."""

    def test_equal_ints(self):
        assert _values_equal(1, 1)

    def test_equal_strings(self):
        assert _values_equal("ADD", "ADD")

    def test_not_equal_strings(self):
        assert not _values_equal("ADD", "MULTIPLY")

    def test_float_tolerance(self):
        assert _values_equal(0.5, 0.5000000001)

    def test_float_not_equal(self):
        assert not _values_equal(0.5, 0.6)

    def test_equal_lists(self):
        assert _values_equal([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

    def test_nested_lists(self):
        assert _values_equal([[1, 2], [3, 4]], [[1, 2], [3, 4]])

    def test_list_length_mismatch(self):
        assert not _values_equal([1, 2], [1, 2, 3])

    def test_type_mismatch(self):
        assert not _values_equal(1, "1")

    def test_bool_vs_int(self):
        assert _values_equal(True, True)
        assert not _values_equal(True, False)


class TestV4DeltaEncoding:
    """Tests for compact format (delta encoding against NODE_DEFAULTS)."""

    def _math_node_data(self, operation="ADD", inputs=None):
        if inputs is None:
            inputs = [0.5, 0.5, 0.5]
        return {
            "nodes": {
                "Math": {
                    "type": "ShaderNodeMath",
                    "name": "Math",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                    "operation": operation,
                    "inputs": inputs,
                }
            },
            "links": [],
            "name": "Material",
        }

    def test_default_props_stripped(self):
        """Props matching defaults should not appear in compact data."""
        data = self._math_node_data(operation="ADD")
        compact = _compact_data(data)
        node = compact["n"][0]
        props = node[5]
        assert "operation" not in props

    def test_non_default_props_preserved(self):
        """Props differing from defaults should be kept."""
        data = self._math_node_data(operation="MULTIPLY")
        compact = _compact_data(data)
        node = compact["n"][0]
        props = node[5]
        assert props["operation"] == "MULTIPLY"

    def test_default_inputs_stripped(self):
        """Inputs matching defaults should not appear in sparse list."""
        data = self._math_node_data(inputs=[0.5, 0.5, 0.5])
        compact = _compact_data(data)
        node = compact["n"][0]
        inputs_sparse = node[6]
        assert inputs_sparse == []

    def test_non_default_inputs_preserved(self):
        """Inputs differing from defaults should survive round-trip."""
        data = self._math_node_data(inputs=[3.0, 0.5, 0.5])
        compact = _compact_data(data)
        node = compact["n"][0]
        inputs_sparse = node[6]
        assert inputs_sparse == [[0, 3.0]]

    def test_roundtrip_all_defaults(self):
        """All-default node should round-trip correctly."""
        data = self._math_node_data()
        decoded = decode(encode(data))
        nd = decoded["nodes"]["Math"]
        assert nd["operation"] == "ADD"
        assert nd["inputs"] == [0.5, 0.5, 0.5]

    def test_roundtrip_non_default_values(self):
        """Non-default values should survive round-trip."""
        data = self._math_node_data(operation="POWER", inputs=[2.0, 3.0, 0.5])
        decoded = decode(encode(data))
        nd = decoded["nodes"]["Math"]
        assert nd["operation"] == "POWER"
        assert nd["inputs"][0] == 2.0
        assert nd["inputs"][1] == 3.0

    def test_unknown_node_type_no_stripping(self):
        """Nodes not in NODE_DEFAULTS should encode/decode without stripping."""
        data = {
            "nodes": {
                "X": {
                    "type": "SomeUnknownNode",
                    "name": "X",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                    "my_prop": "hello",
                    "inputs": [1.0, 2.0],
                }
            },
            "links": [],
            "name": "T",
        }
        decoded = decode(encode(data))
        nd = decoded["nodes"]["X"]
        assert nd["my_prop"] == "hello"
        assert nd["inputs"] == [1.0, 2.0]

    def test_version_is_4(self):
        """Compact data should use version 4."""
        data = self._math_node_data()
        compact = _compact_data(data)
        assert compact["v"] == 4

    def test_principled_bsdf_all_defaults_small(self):
        """Principled BSDF with all defaults should compress to minimal data."""
        defaults = NODE_DEFAULTS["ShaderNodeBsdfPrincipled"]
        data = {
            "nodes": {
                "PBSDF": {
                    "type": "ShaderNodeBsdfPrincipled",
                    "name": "PBSDF",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                    "distribution": defaults["props"]["distribution"],
                    "subsurface_method": defaults["props"]["subsurface_method"],
                    "inputs": list(defaults["inputs"]),
                }
            },
            "links": [],
            "name": "M",
        }
        compact = _compact_data(data)
        node = compact["n"][0]
        assert node[5] == {}
        assert node[6] == []
        decoded = decode(encode(data))
        nd = decoded["nodes"]["PBSDF"]
        assert nd["distribution"] == defaults["props"]["distribution"]
        assert nd["inputs"] == list(defaults["inputs"])

    def test_rgb_node_outputs_preserved(self):
        """ShaderNodeRGB stores its color in outputs -- must survive hash round-trip."""
        color = [0.065, 1.0, 0.066, 1.0]
        data = {
            "nodes": {
                "Color": {
                    "type": "ShaderNodeRGB",
                    "name": "Color",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                    "inputs": [],
                    "outputs": [color],
                }
            },
            "links": [],
            "name": "M",
        }
        decoded = decode(encode(data))
        nd = decoded["nodes"]["Color"]
        assert nd["outputs"][0] == color

    def test_value_node_outputs_preserved(self):
        """ShaderNodeValue stores its value in outputs -- must survive hash round-trip."""
        data = {
            "nodes": {
                "Value": {
                    "type": "ShaderNodeValue",
                    "name": "Value",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                    "inputs": [],
                    "outputs": [0.75],
                }
            },
            "links": [],
            "name": "M",
        }
        decoded = decode(encode(data))
        nd = decoded["nodes"]["Value"]
        assert nd["outputs"][0] == 0.75

    def test_outputs_preserved_json_short(self):
        """Outputs must also survive AI JSON round-trip."""
        color = [0.5, 0.2, 0.8, 1.0]
        data = {
            "nodes": {
                "Color": {
                    "type": "ShaderNodeRGB",
                    "name": "Color",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                    "inputs": [],
                    "outputs": [color],
                }
            },
            "links": [],
            "name": "M",
        }
        decoded = decode_ai_json(encode_ai_json(data))
        nd = decoded["nodes"]["Color"]
        assert nd["outputs"][0] == color
