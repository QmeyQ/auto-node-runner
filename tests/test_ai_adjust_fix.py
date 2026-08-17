"""验证 AI 调整修复：
1. match_orchestrate 不再用 exclusivity 过滤短名材质候选
2. _parse_ai_return_data 能解析外层 {"response": "..."} 包装
3. 提交/解析使用 %pN% 占位符路径，paths 映射不提交给 AI
"""

import json
import os
import sys

import pytest

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PKG_ROOT)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from auto_node_runner import operators


TEX_DIR = r"E:\project\作品\1-2 魔药课\textrues"

FILES = [
    os.path.join(TEX_DIR, "book_02_-_Default_BaseColor.png"),
    os.path.join(TEX_DIR, "book_02_-_Default_Metallic.png"),
    os.path.join(TEX_DIR, "book_02_-_Default_Normal_R.png"),
]


def test_short_material_keeps_candidates_after_long_match():
    """短名材质 Book 应保留长名材质 book_02_-_Defaul 匹配到的文件候选。"""
    materials = ["Book", "book_02_-_Defaul"]
    results, classification = operators.match_orchestrate(materials, FILES)

    book_result = results.get("Book", {})
    long_result = results.get("book_02_-_Defaul", {})

    book_paths = {v for v in book_result.values() if v}
    long_paths = {v for v in long_result.values() if v}

    assert long_paths, "长名材质应匹配到贴图"
    assert book_paths, "短名材质 Book 也应匹配到贴图（不再被 exclusivity 清空）"

    overlap = book_paths & long_paths
    assert overlap, (
        f"Book 与 book_02_-_Defaul 应有重叠候选，book={book_paths}, long={long_paths}"
    )


def test_classification_covers_all_files():
    """match_orchestrate 返回的 classification 应覆盖所有输入文件。"""
    materials = ["Book", "book_02_-_Defaul"]
    _results, classification = operators.match_orchestrate(materials, FILES)

    all_classified = set()
    for mat_name, files in classification.items():
        all_classified.update(files)

    assert all_classified == set(FILES), (
        f"classification 应覆盖全部文件，实际={all_classified}"
    )


def _make_items(materials, results, classification):
    class _Item:
        def __init__(self, mat_name, result, classified):
            self.material_name = mat_name
            self.classified_files = "\n".join(classified)
            for attr, tex_type in zip(operators._TEX_ATTRS, operators._TEX_TYPES):
                setattr(self, attr, result.get(tex_type, ""))

    return [
        _Item(name, results.get(name, {}), classification.get(name, []))
        for name in materials
    ]


def test_build_path_placeholders_root_and_parent_names(tmp_path):
    """根目录用 %C%，其他目录用父目录名。"""
    root = str(tmp_path / "textrues")
    sub_a = os.path.join(str(tmp_path), "初始工程A", "tex")
    sub_b = os.path.join(str(tmp_path), "初始工程B", "tex")
    os.makedirs(root)
    os.makedirs(sub_a)
    os.makedirs(sub_b)

    paths = [
        os.path.join(root, "a.png"),
        os.path.join(sub_a, "b.png"),
        os.path.join(sub_b, "c.png"),
    ]
    d2p, p2d = operators._build_path_placeholders(paths, root_dir=root)

    assert d2p[root] == "%C%"
    assert d2p[sub_a] == "%初始工程A%"
    assert d2p[sub_b] == "%初始工程B%"


def test_build_path_placeholders_collision_adds_suffix(tmp_path):
    """不同路径但相同父目录名时，后续加累加数字。"""
    d1 = os.path.join(str(tmp_path), "proj1", "tex")
    d2 = os.path.join(str(tmp_path), "proj2", "tex")
    os.makedirs(d1)
    os.makedirs(d2)

    paths = [os.path.join(d1, "a.png"), os.path.join(d2, "b.png")]
    d2p, p2d = operators._build_path_placeholders(paths)

    names = {d2p[d1], d2p[d2]}
    assert "%proj1%" in names and "%proj2%" in names


def test_to_and_expand_placeholder_are_inverse():
    """占位符转换与还原互逆。"""
    d1 = os.path.join(TEX_DIR, "d1")
    d2 = os.path.join(TEX_DIR, "d2")
    paths = [
        os.path.join(d1, "1.png"),
        os.path.join(d2, "2.png"),
        os.path.join(d1, "3.png"),
    ]
    d2p, p2d = operators._build_path_placeholders(paths)
    for full in paths:
        ph = operators._to_placeholder_path(full, d2p)
        assert ph.startswith("%"), f"应转为占位符路径: {ph}"
        back = operators._expand_placeholder_path(ph, p2d)
        assert back == full, f"还原应一致: {back} != {full}"


def test_build_ai_submission_uses_placeholder_paths():
    """_build_ai_submission_data 应产出 %name% 占位符路径，且 submission 不含 paths。"""
    materials = ["Book", "book_02_-_Defaul"]
    results, classification = operators.match_orchestrate(materials, FILES)
    items = _make_items(materials, results, classification)

    submission, p2d = operators._build_ai_submission_data(items)

    assert "paths" not in submission, "submission 不应含 paths（不提交给 AI）"
    assert p2d, "placeholder_map 不应为空"

    for mat_name in materials:
        mat_data = submission[mat_name]
        for k, v in mat_data.items():
            if k == "unmatch":
                for ph_path in v:
                    assert ph_path.startswith("%"), (
                        f"unmatch 应为占位符路径: {ph_path}"
                    )
            elif isinstance(v, str) and v:
                assert v.startswith("%"), f"匹配路径应为占位符: {v}"


def test_build_ai_submission_unmatch_covers_unmatched_candidates():
    """unmatch 占位符路径还原后应等于候选文件减去已匹配文件。"""
    materials = ["Book", "book_02_-_Defaul"]
    results, classification = operators.match_orchestrate(materials, FILES)
    items = _make_items(materials, results, classification)

    submission, p2d = operators._build_ai_submission_data(items)

    for mat_name in materials:
        matched_full = set()
        for attr, tex_type in zip(operators._TEX_ATTRS, operators._TEX_TYPES):
            ph_v = submission[mat_name].get(tex_type, "")
            if ph_v:
                matched_full.add(operators._expand_placeholder_path(ph_v, p2d))

        unmatch_full = {
            operators._expand_placeholder_path(ph, p2d)
            for ph in submission[mat_name]["unmatch"]
        }
        expected = set(classification.get(mat_name, [])) - matched_full
        assert unmatch_full == expected, (
            f"unmatch 还原后应为候选减已匹配，{mat_name}: got={unmatch_full}, want={expected}"
        )


def test_parse_ai_return_with_response_wrapper():
    """_parse_ai_return_data 应解析 {"response": "..."} 外层包装并还原占位符。"""
    p2d = {"%p0%": TEX_DIR}
    bc_ph = "%p0%/book_02_-_Default_BaseColor.png"
    inner_json = json.dumps(
        {
            "Book": {"basecolor": bc_ph},
            "book_02_-_Defaul": {"basecolor": bc_ph},
        },
        ensure_ascii=False,
    )
    wrapped = json.dumps({"response": inner_json}, ensure_ascii=False)

    submission = {
        "Book": {"basecolor": bc_ph, "unmatch": []},
        "book_02_-_Defaul": {"basecolor": bc_ph, "unmatch": []},
    }

    result = operators._parse_ai_return_data(wrapped, submission, p2d)
    assert result["Book"]["basecolor"] == FILES[0]
    assert result["book_02_-_Defaul"]["basecolor"] == FILES[0]


def test_parse_ai_return_with_response_wrapper_chinese():
    """response 内含中文时不应出现 \\uXXXX 转义干扰解析。"""
    p2d = {"%p0%": TEX_DIR}
    bc_ph = "%p0%/book_02_-_Default_BaseColor.png"
    inner = (
        '```json\n'
        + json.dumps({"Book": {"basecolor": bc_ph}}, ensure_ascii=False)
        + '\n```'
    )
    wrapped = json.dumps({"response": inner}, ensure_ascii=False)

    submission = {"Book": {"basecolor": bc_ph, "unmatch": []}}
    result = operators._parse_ai_return_data(wrapped, submission, p2d)
    assert result["Book"]["basecolor"] == FILES[0]


def test_parse_ai_return_error_wrapper():
    """外层 {"error": "..."} 应抛出 ValueError。"""
    wrapped = json.dumps({"error": "model load failed"}, ensure_ascii=False)
    with pytest.raises(ValueError, match="AI error"):
        operators._parse_ai_return_data(wrapped, {})


def test_text_py_response_no_ascii_escape():
    """Text.py 输出的 response 不应包含 \\u 转义（中文原样输出）。"""
    full_response = "好的，调整结果如下"
    out = json.dumps({"response": full_response}, ensure_ascii=False)
    assert "好的" in out
    assert "\\u" not in out


def test_parse_ai_return_accepts_placeholder_from_unmatch():
    """AI 返回 unmatch 中的占位符路径应被接受并还原为完整路径。"""
    p2d = {"%p0%": TEX_DIR}
    extra_ph = "%p0%/extra.png"
    submission = {
        "Book": {"basecolor": "%p0%/book_02_-_Default_BaseColor.png", "unmatch": [extra_ph]},
    }
    ai_return = json.dumps({"Book": {"basecolor": extra_ph}}, ensure_ascii=False)

    result = operators._parse_ai_return_data(ai_return, submission, p2d)
    assert result["Book"]["basecolor"] == os.path.join(TEX_DIR, "extra.png")


def test_parse_ai_return_skips_nonexistent_path(capfd):
    """AI 返回候选外且不存在的占位符路径应被跳过，并 print 提示。"""
    p2d = {"%p0%": TEX_DIR}
    submission = {
        "Book": {"basecolor": "%p0%/book_02_-_Default_BaseColor.png", "unmatch": []},
    }
    bogus_ph = "%p0%/Book_Normal_R.png"
    ai_return = json.dumps({"Book": {"basecolor": bogus_ph}}, ensure_ascii=False)

    result = operators._parse_ai_return_data(ai_return, submission, p2d)
    assert "basecolor" not in result["Book"], "不存在的路径不应写入结果"

    captured = capfd.readouterr()
    assert "skipped non-existent AI return" in captured.out
    assert "Book.basecolor" in captured.out


def test_parse_ai_return_accepts_existing_out_of_candidate(tmp_path, capfd):
    """AI 返回候选外但真实存在的文件（占位符路径）应被接受。"""
    real_file = tmp_path / "extra.png"
    real_file.write_bytes(b"\x89PNG")
    real_dir = str(tmp_path)

    p2d = {"%p0%": TEX_DIR, "%p1%": real_dir}
    submission = {
        "Book": {"basecolor": "%p0%/book_02_-_Default_BaseColor.png", "unmatch": []},
    }
    ai_return = json.dumps(
        {"Book": {"basecolor": "%p1%/extra.png"}}, ensure_ascii=False
    )

    result = operators._parse_ai_return_data(ai_return, submission, p2d)
    assert result["Book"]["basecolor"] == str(real_file)

    captured = capfd.readouterr()
    assert "accepted existing path outside candidates" in captured.out


def test_parse_ai_return_with_user_real_data_structure(tmp_path):
    """用用户提供的实际数据结构验证端到端解析（含 %p0/%p1/%p2 多占位符）。"""
    d0 = str(tmp_path / "tex")
    d1 = str(tmp_path / "sub")
    d2 = str(tmp_path / "other")
    os.makedirs(d0)
    os.makedirs(d1)
    os.makedirs(d2)

    p2d = {"%p0%": d0, "%p1%": d1, "%p2%": d2}

    submission = {
        "Book": {
            "basecolor": "%p0%/Book_BaseColor.png",
            "metallic": "%p0%/Book_Metallic.png",
            "unmatch": [
                "%p0%/book_02_-_Default_Roughness.png",
                "%p1%/Book_Normal.png",
                "%p2%/RUST_02_soft_base_tiled_bw.tif",
            ],
        },
        "book_02_-_Defaul": {
            "basecolor": "%p0%/book_02_-_Default_BaseColor.png",
            "unmatch": ["%p0%/Book_Roughness.png", "%p1%/Book_BaseColor.png"],
        },
    }

    ai_return = json.dumps(
        {
            "Book": {
                "basecolor": "%p0%/Book_BaseColor.png",
                "metallic": "%p0%/Book_Metallic.png",
                "roughness": "%p1%/Book_Normal.png",
                "normal": "%p1%/Book_Normal.png",
            },
            "book_02_-_Defaul": {
                "basecolor": "%p0%/book_02_-_Default_BaseColor.png",
            },
        },
        ensure_ascii=False,
    )

    result = operators._parse_ai_return_data(ai_return, submission, p2d)

    assert result["Book"]["basecolor"] == os.path.join(d0, "Book_BaseColor.png")
    assert result["Book"]["metallic"] == os.path.join(d0, "Book_Metallic.png")
    assert result["Book"]["roughness"] == os.path.join(d1, "Book_Normal.png")
    assert result["Book"]["normal"] == os.path.join(d1, "Book_Normal.png")
    assert result["book_02_-_Defaul"]["basecolor"] == os.path.join(
        d0, "book_02_-_Default_BaseColor.png"
    )
