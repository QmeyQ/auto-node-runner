"""Tests for the encoding module."""

import base64
import json
import pickle
import xml.etree.ElementTree as ET
import zlib

import pytest

from node_runner.encoding import (
    encode,
    decode,
    encode_json,
    decode_json,
    encode_ai_json,
    decode_ai_json,
    encode_xml,
    decode_xml,
    encode_as,
    decode_as,
    detect_format,
    FORMAT_HASH,
    FORMAT_JSON,
    FORMAT_AI_JSON,
    FORMAT_XML,
)


class TestEncodeDecode:
    """Round-trip tests for encode/decode."""

    def test_roundtrip_simple_dict(self):
        data = {"nodes": {}, "links": [], "name": "Test"}
        encoded = encode(data)
        assert isinstance(encoded, str)
        assert len(encoded) > 0
        decoded = decode(encoded)
        assert decoded["nodes"] == {}
        assert decoded["links"] == []
        assert decoded["name"] == "Test"

    def test_roundtrip_with_node_data(self):
        data = {
            "nodes": {
                "Math": {
                    "type": "ShaderNodeMath",
                    "label": "",
                    "operation": "ADD",
                    "location": [100.0, 200.0],
                    "location_absolute": [100.0, 200.0],
                    "inputs": [0.5, 0.5, None],
                }
            },
            "links": [],
            "name": "Material",
        }
        encoded = encode(data)
        decoded = decode(encoded)
        assert decoded["nodes"]["Math"]["operation"] == "ADD"
        assert decoded["nodes"]["Math"]["location"] == [100.0, 200.0]

    def test_roundtrip_with_links(self):
        data = {
            "nodes": {"A": {"type": "ShaderNodeMath"}, "B": {"type": "ShaderNodeMath"}},
            "links": [
                {
                    "from_node": "A",
                    "to_node": "B",
                    "from_socket": "Value",
                    "from_socket_type": "NodeSocketFloat",
                    "from_socket_identifier": "Value",
                    "to_socket": "Value",
                    "to_socket_type": "NodeSocketFloat",
                    "to_socket_identifier": "Value",
                }
            ],
            "name": "Test",
        }
        decoded = decode(encode(data))
        # Compact format preserves semantic content
        assert decoded["nodes"]["A"]["type"] == "ShaderNodeMath"
        assert decoded["nodes"]["B"]["type"] == "ShaderNodeMath"
        assert len(decoded["links"]) == 1
        link = decoded["links"][0]
        assert link["from_node"] == "A"
        assert link["to_node"] == "B"
        assert link["from_socket_identifier"] == "Value"
        assert link["to_socket_identifier"] == "Value"

    def test_decode_invalid_data(self):
        with pytest.raises(ValueError, match="Failed to decode"):
            decode("not-valid-base64!!!")

    def test_decode_empty_string(self):
        with pytest.raises(ValueError):
            decode("")

    def test_roundtrip_nested_structures(self):
        data = {
            "nodes": {
                "ColorRamp": {
                    "type": "ShaderNodeValToRGB",
                    "color_ramp": {
                        "color_mode": "RGB",
                        "interpolation": "LINEAR",
                        "hue_interpolation": "NEAR",
                        "elements": [
                            {"position": 0.0, "color": [0.0, 0.0, 0.0, 1.0]},
                            {"position": 1.0, "color": [1.0, 1.0, 1.0, 1.0]},
                        ],
                    },
                }
            },
            "links": [],
            "name": "Test",
        }
        decoded = decode(encode(data))
        ramp = decoded["nodes"]["ColorRamp"]["color_ramp"]
        assert ramp["color_mode"] == "RGB"
        assert ramp["interpolation"] == "LINEAR"
        assert ramp["hue_interpolation"] == "NEAR"
        assert len(ramp["elements"]) == 2
        assert ramp["elements"][0]["position"] == 0.0
        assert ramp["elements"][1]["color"] == [1.0, 1.0, 1.0, 1.0]

    def test_encode_uses_json_not_pickle(self):
        """New format should use JSON internally, not pickle."""
        data = {"nodes": {}, "links": [], "name": "Test"}
        encoded = encode(data)
        compressed = base64.b64decode(encoded)
        raw = zlib.decompress(compressed)
        # Should be valid JSON
        parsed = json.loads(raw)
        assert parsed["v"] >= 2  # compact format version

    def test_compact_sparse_inputs(self):
        """None values in inputs should be stripped in compact format.

        Defaults are restored for known node types, so None values whose
        index falls within the defaults table come back as the default
        rather than None.  Use a node type not in the defaults table so that
        the sparse-input round-trip preserves None exactly.
        """
        data = {
            "nodes": {
                "Custom": {
                    "type": "ShaderNodeCustom",
                    "inputs": [0.5, None, None, 0.3],
                    "name": "Custom",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                }
            },
            "links": [],
            "name": "T",
        }
        decoded = decode(encode(data))
        inputs = decoded["nodes"]["Custom"]["inputs"]
        assert inputs[0] == 0.5
        assert inputs[1] is None
        assert inputs[2] is None
        assert inputs[3] == 0.3

    def test_compact_link_indices(self):
        """Links should use node indices internally."""
        data = {
            "nodes": {
                "A": {"type": "ShaderNodeMath"},
                "B": {"type": "ShaderNodeMath"},
            },
            "links": [
                {
                    "from_node": "A",
                    "to_node": "B",
                    "from_socket": "Value",
                    "from_socket_identifier": "Value",
                    "to_socket": "Value",
                    "to_socket_identifier": "Value",
                }
            ],
            "name": "T",
        }
        encoded = encode(data)
        raw = zlib.decompress(base64.b64decode(encoded))
        compact = json.loads(raw)
        # Links use integer node indices internally
        assert compact["l"][0][0] == 0  # from node A (index 0)
        assert compact["l"][0][2] == 1  # to node B (index 1)

    def test_compact_preserves_metadata(self):
        """blender_version and export_name survive compaction."""
        data = {
            "nodes": {},
            "links": [],
            "name": "Test",
            "blender_version": "4.5.0",
            "export_name": "MyExport",
        }
        decoded = decode(encode(data))
        assert decoded["blender_version"] == "4.5.0"
        assert decoded["export_name"] == "MyExport"

    def test_compact_position_quantization(self):
        """Positions should be rounded to integers."""
        data = {
            "nodes": {
                "N": {
                    "type": "ShaderNodeMath",
                    "location_absolute": [123.7, -456.2],
                }
            },
            "links": [],
            "name": "T",
        }
        raw = zlib.decompress(base64.b64decode(encode(data)))
        compact = json.loads(raw)
        loc = compact["n"][0][3]  # location field
        assert loc == [124, -456]  # rounded
        # But expanded back to floats
        decoded = decode(encode(data))
        assert decoded["nodes"]["N"]["location_absolute"] == [124.0, -456.0]

    def test_backward_compat_legacy_pickle(self):
        """Old pickle-encoded strings should still decode."""
        data = {"nodes": {}, "links": [], "name": "Legacy"}
        # Simulate old pickle format
        raw = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
        compressed = zlib.compress(raw, 9)
        old_encoded = base64.b64encode(compressed).decode("utf-8")
        decoded = decode(old_encoded)
        assert decoded == data


# Sample data used by JSON / XML tests

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

_NESTED_DATA = {
    "nodes": {
        "ColorRamp": {
            "type": "ShaderNodeValToRGB",
            "color_ramp": {
                "color_mode": "RGB",
                "interpolation": "LINEAR",
                "hue_interpolation": "NEAR",
                "elements": [
                    {"position": 0.0, "color": [0.0, 0.0, 0.0, 1.0]},
                    {"position": 1.0, "color": [1.0, 1.0, 1.0, 1.0]},
                ],
            },
        }
    },
    "links": [],
    "name": "Test",
}


class TestJsonEncodeDecode:
    """Round-trip tests for JSON encode/decode."""

    def test_roundtrip_simple_dict(self):
        encoded = encode_json(_SIMPLE_DATA)
        assert isinstance(encoded, str)
        decoded = decode_json(encoded)
        assert decoded == _SIMPLE_DATA

    def test_roundtrip_with_node_data(self):
        encoded = encode_json(_NODE_DATA)
        decoded = decode_json(encoded)
        assert decoded["nodes"]["Math"]["operation"] == "ADD"
        assert decoded["nodes"]["Math"]["location"] == [100.0, 200.0]

    def test_roundtrip_nested_structures(self):
        assert decode_json(encode_json(_NESTED_DATA)) == _NESTED_DATA

    def test_output_is_human_readable(self):
        encoded = encode_json(_SIMPLE_DATA)
        assert "\n" in encoded
        assert '"name"' in encoded

    def test_output_is_valid_json(self):
        """Exported JSON must be a valid standalone document."""
        data_with_name = dict(_SIMPLE_DATA, export_name="MyNodes")
        encoded = encode_json(data_with_name)
        parsed = json.loads(encoded)
        assert parsed["export_name"] == "MyNodes"

    def test_decode_invalid_json(self):
        with pytest.raises(ValueError, match="Failed to decode JSON"):
            decode_json("{invalid json!!}")

    def test_decode_empty_string(self):
        with pytest.raises(ValueError):
            decode_json("")

    def test_preserves_none_values(self):
        data = {"nodes": {"A": {"value": None}}, "links": [], "name": "T"}
        assert decode_json(encode_json(data)) == data

    def test_preserves_bool_values(self):
        data = {"nodes": {"A": {"flag": True, "off": False}}, "links": [], "name": "T"}
        assert decode_json(encode_json(data)) == data

    def test_export_name_roundtrip(self):
        data = dict(_SIMPLE_DATA, export_name="TestExport")
        decoded = decode_json(encode_json(data))
        assert decoded["export_name"] == "TestExport"
        # After popping, original data is recovered
        decoded.pop("export_name")
        assert decoded == _SIMPLE_DATA


class TestXmlEncodeDecode:
    """Round-trip tests for XML encode/decode."""

    def test_roundtrip_simple_dict(self):
        encoded = encode_xml(_SIMPLE_DATA)
        assert isinstance(encoded, str)
        decoded = decode_xml(encoded)
        assert decoded == _SIMPLE_DATA

    def test_roundtrip_with_node_data(self):
        encoded = encode_xml(_NODE_DATA)
        decoded = decode_xml(encoded)
        assert decoded["nodes"]["Math"]["operation"] == "ADD"
        assert decoded["nodes"]["Math"]["location"] == [100.0, 200.0]

    def test_roundtrip_nested_structures(self):
        assert decode_xml(encode_xml(_NESTED_DATA)) == _NESTED_DATA

    def test_output_is_xml(self):
        encoded = encode_xml(_SIMPLE_DATA)
        assert encoded.startswith("<?xml")
        assert "<node_runner" in encoded

    def test_output_is_valid_xml(self):
        """Exported XML must be a valid standalone document."""
        data_with_name = dict(_SIMPLE_DATA, export_name="MyNodes")
        encoded = encode_xml(data_with_name)
        root = ET.fromstring(encoded)
        assert root.tag == "node_runner"

    def test_decode_invalid_xml(self):
        with pytest.raises(ValueError, match="Failed to decode XML"):
            decode_xml("<unclosed")

    def test_decode_empty_string(self):
        with pytest.raises(ValueError):
            decode_xml("")

    def test_preserves_none_values(self):
        data = {"nodes": {"A": {"value": None}}, "links": [], "name": "T"}
        assert decode_xml(encode_xml(data)) == data

    def test_preserves_bool_values(self):
        data = {"nodes": {"A": {"flag": True, "off": False}}, "links": [], "name": "T"}
        assert decode_xml(encode_xml(data)) == data

    def test_preserves_int_vs_float(self):
        data = {"nodes": {}, "links": [], "name": "T", "count": 42, "ratio": 3.14}
        result = decode_xml(encode_xml(data))
        assert isinstance(result["count"], int)
        assert isinstance(result["ratio"], float)

    def test_export_name_roundtrip(self):
        data = dict(_SIMPLE_DATA, export_name="TestExport")
        decoded = decode_xml(encode_xml(data))
        assert decoded["export_name"] == "TestExport"
        decoded.pop("export_name")
        assert decoded == _SIMPLE_DATA


class TestAiJsonEncodeDecode:
    """Round-trip tests for AI JSON (compact readable) encode/decode."""

    def test_roundtrip_simple_dict(self):
        encoded = encode_ai_json(_SIMPLE_DATA)
        assert isinstance(encoded, str)
        decoded = decode_ai_json(encoded)
        assert decoded["nodes"] == {}
        assert decoded["links"] == []
        assert decoded["name"] == "Test"

    def test_roundtrip_with_node_data(self):
        """Math node with non-default operation should round-trip."""
        data = {
            "nodes": {
                "Math": {
                    "type": "ShaderNodeMath",
                    "label": "",
                    "operation": "MULTIPLY",
                    "location": [100.0, 200.0],
                    "location_absolute": [100.0, 200.0],
                    "inputs": [0.5, 0.5],
                }
            },
            "links": [],
            "name": "Material",
        }
        encoded = encode_ai_json(data)
        decoded = decode_ai_json(encoded)
        assert decoded["nodes"]["Math"]["operation"] == "MULTIPLY"

    def test_output_is_readable_json(self):
        encoded = encode_ai_json(_NODE_DATA)
        assert "\n" in encoded
        parsed = json.loads(encoded)
        assert "nodes" in parsed  # AI-readable format
        assert "links" in parsed
        # Should use named socket format, not compact arrays
        assert "v" not in parsed
        assert "n" not in parsed

    def test_shorter_than_full_json(self):
        full = encode_json(_NODE_DATA)
        short = encode_ai_json(_NODE_DATA)
        assert len(short) < len(full)

    def test_strips_default_props(self):
        data = {
            "nodes": {
                "A": {
                    "type": "ShaderNodeMath",
                    "label": "",
                    "name": "A",
                    "location_absolute": [0.0, 0.0],
                    "mute": False,
                    "show_preview": False,
                    "use_custom_color": False,
                    "color": [
                        0.6079999804496765,
                        0.6079999804496765,
                        0.6079999804496765,
                    ],
                    "inputs": [0.5],
                }
            },
            "links": [],
            "name": "T",
        }
        encoded = encode_ai_json(data)
        parsed = json.loads(encoded)
        node = parsed["nodes"]["A"]
        # Default props should be stripped
        assert "mute" not in node.get("settings", {})
        assert "show_preview" not in node.get("settings", {})
        assert "use_custom_color" not in node.get("settings", {})
        assert "color" not in node.get("settings", {})

    def test_decode_invalid_json(self):
        with pytest.raises(ValueError, match="Failed to decode"):
            decode_ai_json("{invalid}")

    def test_export_name_roundtrip(self):
        data = dict(_SIMPLE_DATA, export_name="AIChat")
        decoded = decode_ai_json(encode_ai_json(data))
        assert decoded["export_name"] == "AIChat"

    def test_named_inputs_and_links(self):
        """AI JSON should use named sockets and readable links."""
        data = {
            "nodes": {
                "Noise": {
                    "type": "ShaderNodeTexNoise",
                    "label": "",
                    "location_absolute": [0.0, 0.0],
                    "inputs": [
                        [0.0, 0.0, 0.0],
                        0.0,
                        8.0,
                        2.0,
                        0.5,
                        2.0,
                        0.0,
                        0.0,
                        1.0,
                        1.0,
                    ],
                },
                "Math": {
                    "type": "ShaderNodeMath",
                    "label": "",
                    "operation": "MULTIPLY",
                    "location_absolute": [200.0, 0.0],
                    "inputs": [0.5, 2.0, 0.5],
                },
            },
            "links": [
                {
                    "from_node": "Noise",
                    "to_node": "Math",
                    "from_socket": "Fac",
                    "from_socket_identifier": "Fac",
                    "to_socket": "Value",
                    "to_socket_identifier": "Value",
                }
            ],
            "name": "Test",
        }
        encoded = encode_ai_json(data)
        parsed = json.loads(encoded)

        # Named inputs
        noise = parsed["nodes"]["Noise"]
        assert "inputs" in noise
        assert noise["inputs"]["Scale"] == 8.0  # non-default (default is 5.0)
        assert "Detail" not in noise["inputs"]  # 2.0 is the default

        # Non-default operation under settings
        math = parsed["nodes"]["Math"]
        assert math["settings"]["operation"] == "MULTIPLY"
        assert math["inputs"]["Value"] == 2.0

        # Readable link format
        assert parsed["links"][0] == ["Noise.Fac", "Math.Value"]

        # Round-trip
        decoded = decode_ai_json(encoded)
        assert decoded["nodes"]["Math"]["operation"] == "MULTIPLY"

    def test_legacy_compact_decode(self):
        """decode_ai_json should still handle legacy compact format."""
        compact = (
            '{"v":4,"n":[["ShaderNodeMath","M","M",[0,0],-1,'
            '{"operation":"ADD"},[]]],"l":[],"name":"T"}'
        )
        decoded = decode_ai_json(compact)
        assert decoded["nodes"]["M"]["operation"] == "ADD"

    def test_duplicate_socket_links_encode(self):
        """Encode should disambiguate duplicate target socket names."""
        data = {
            "nodes": {
                "BSDF1": {
                    "type": "ShaderNodeBsdfPrincipled",
                    "location_absolute": [0.0, 0.0],
                    "inputs": [],
                },
                "BSDF2": {
                    "type": "ShaderNodeBsdfGlass",
                    "location_absolute": [0.0, -200.0],
                    "inputs": [],
                },
                "Mix": {
                    "type": "ShaderNodeMixShader",
                    "location_absolute": [300.0, 0.0],
                    "inputs": [0.5],
                },
            },
            "links": [
                {
                    "from_node": "BSDF1",
                    "to_node": "Mix",
                    "from_socket": "BSDF",
                    "from_socket_identifier": "BSDF",
                    "to_socket": "Shader",
                    "to_socket_identifier": "Shader",
                },
                {
                    "from_node": "BSDF2",
                    "to_node": "Mix",
                    "from_socket": "BSDF",
                    "from_socket_identifier": "BSDF",
                    "to_socket": "Shader",
                    "to_socket_identifier": "Shader",
                },
            ],
            "name": "DupeSockets",
        }
        encoded = encode_ai_json(data)
        parsed = json.loads(encoded)
        links = parsed["links"]
        assert len(links) == 2
        # First link uses bare name, second should be disambiguated
        assert links[0] == ["BSDF1.BSDF", "Mix.Shader"]
        assert links[1] == ["BSDF2.BSDF", "Mix.Shader 2"]

    def test_duplicate_socket_links_decode_disambiguated(self):
        """Decode should resolve 'Shader 2' to socket index 2."""
        ai_json = json.dumps(
            {
                "nodes": {
                    "BSDF1": {"type": "ShaderNodeBsdfPrincipled", "location": [0, 0]},
                    "BSDF2": {"type": "ShaderNodeBsdfGlass", "location": [0, -200]},
                    "Mix": {"type": "ShaderNodeMixShader", "location": [300, 0]},
                },
                "links": [
                    ["BSDF1.BSDF", "Mix.Shader"],
                    ["BSDF2.BSDF", "Mix.Shader 2"],
                ],
                "name": "DupeSockets",
            }
        )
        decoded = decode_ai_json(ai_json)
        links = decoded["links"]
        assert len(links) == 2
        assert links[0]["to_socket"] == "Shader"
        assert links[1]["to_socket"] == "Shader"
        assert links[1]["to_socket_index"] == 2

    def test_duplicate_socket_links_decode_bare(self):
        """Decode should resolve bare duplicate names by link order."""
        ai_json = json.dumps(
            {
                "nodes": {
                    "BSDF1": {"type": "ShaderNodeBsdfPrincipled", "location": [0, 0]},
                    "BSDF2": {"type": "ShaderNodeBsdfGlass", "location": [0, -200]},
                    "Mix": {"type": "ShaderNodeMixShader", "location": [300, 0]},
                },
                "links": [
                    ["BSDF1.BSDF", "Mix.Shader"],
                    ["BSDF2.BSDF", "Mix.Shader"],
                ],
                "name": "DupeSockets",
            }
        )
        decoded = decode_ai_json(ai_json)
        links = decoded["links"]
        assert len(links) == 2
        # Both have name "Shader", but indices differ
        assert links[0]["to_socket_index"] == 1
        assert links[1]["to_socket_index"] == 2


class TestEncodeAsDecodeAs:
    """Tests for the unified encode_as / decode_as API."""

    @pytest.mark.parametrize(
        "fmt", [FORMAT_HASH, FORMAT_JSON, FORMAT_AI_JSON, FORMAT_XML]
    )
    def test_roundtrip_all_formats(self, fmt):
        encoded = encode_as(_NODE_DATA, fmt)
        decoded = decode_as(encoded, fmt)
        assert decoded["name"] == "Material"
        assert "Math" in decoded["nodes"]
        # HASH restores defaults; JSON_SHORT strips defaults but
        # preserves non-default values; JSON/XML preserve everything.
        if fmt == FORMAT_JSON:
            assert decoded == _NODE_DATA
        if fmt in (FORMAT_HASH,):
            # Restores operation default
            assert decoded["nodes"]["Math"]["operation"] == "ADD"

    def test_default_is_hash(self):
        assert encode_as(_SIMPLE_DATA) == encode(_SIMPLE_DATA)

    def test_unknown_format_encode(self):
        with pytest.raises(ValueError, match="Unknown format"):
            encode_as(_SIMPLE_DATA, "TOML")

    def test_unknown_format_decode(self):
        with pytest.raises(ValueError, match="Unknown format"):
            decode_as("data", "TOML")


class TestDetectFormat:
    """Tests for auto-detection of format from raw strings."""

    def test_detect_json_object(self):
        assert detect_format('{"nodes": {}}') == FORMAT_JSON

    def test_detect_json_array(self):
        assert detect_format("[1, 2, 3]") == FORMAT_JSON

    def test_detect_xml(self):
        assert detect_format("<?xml version='1.0'?>") == FORMAT_XML

    def test_detect_xml_element(self):
        assert detect_format("<node_runner>") == FORMAT_XML

    def test_detect_hash(self):
        assert detect_format("eJzLSM3JyQcABJgB8Q==") == FORMAT_HASH

    def test_detect_with_whitespace(self):
        assert detect_format('  \n  {"a": 1}') == FORMAT_JSON
