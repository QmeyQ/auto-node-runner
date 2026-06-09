"""
Internationalization (i18n) module for Auto Node Runner.

Provides translation lookup for en_US and zh_CN languages.
Automatically follows Blender system language, with manual override option.
Default is AUTO (follow system).
"""

import bpy

_PKG = __package__.rpartition(".")[2] if "." in __package__ else __package__

# [Auto Texture] Translation dictionary for en_US and zh_CN - Modified: 2026-06-09
_TRANSLATIONS = {
    "en_US": {
        "panel.title": "Auto Texture",
        "panel.category": "Auto Texture",
        "label.resource_directory": "Resource Directory:",
        "label.basecolor": "Base Color",
        "label.metallic": "Metallic",
        "label.roughness": "Roughness",
        "label.normal": "Normal",
        "label.ao": "AO",
        "button.match_textures": "Match Textures ({count} selected)",
        "button.apply_all": "Apply All ({count} unfilled)",
        "button.clear": "",
        "button.select_directory": "",
        "message.texture_not_found": "Texture not found",
        "message.directory_not_exist": "Directory path does not exist",
        "message.select_object_first": "Please select an object with materials first",
        "message.matched_count": "Matched {count} materials",
        "message.no_data_to_apply": "No data to apply",
        "message.applied_count": "Applied {applied} materials, {missing} textures not found",
        "message.select_directory_first": "Please select a resource directory first",
        "message.invalid_directory": "Invalid resource directory",
        "message.template_load_failed": "Template file [{name}] load failed, material [{mat}] skipped",
        "message.material_deleted": "Material [{mat}] no longer exists, skipped",
        "prefs.language": "Language",
        "prefs.language_desc": "Interface language (AUTO follows Blender system language)",
    },
    "zh_CN": {
        "panel.title": "自动贴图",
        "panel.category": "自动贴图",
        "label.resource_directory": "资源目录:",
        "label.basecolor": "基础色",
        "label.metallic": "金属度",
        "label.roughness": "粗糙度",
        "label.normal": "法线",
        "label.ao": "AO",
        "button.match_textures": "匹配贴图（已选 {count} 个材质）",
        "button.apply_all": "一键应用（{count} 个未填）",
        "button.clear": "",
        "button.select_directory": "",
        "message.texture_not_found": "未找到贴图",
        "message.directory_not_exist": "目录路径不存在",
        "message.select_object_first": "请先选择包含材质的物体",
        "message.matched_count": "已匹配 {count} 个材质",
        "message.no_data_to_apply": "没有可应用的数据",
        "message.applied_count": "已应用 {applied} 个材质, {missing} 个贴图未找到",
        "message.select_directory_first": "请先选择资源目录",
        "message.invalid_directory": "资源目录无效",
        "message.template_load_failed": "模板文件[{name}]加载失败，材质[{mat}]跳过",
        "message.material_deleted": "材质[{mat}]已不存在，已跳过",
        "prefs.language": "语言",
        "prefs.language_desc": "界面语言（自动跟随Blender系统语言）",
    },
}


# [Auto Texture] Resolve language setting from addon preferences, fallback to AUTO - Modified: 2026-06-09
def _resolve_language():
    try:
        prefs = bpy.context.preferences.addons[_PKG].preferences
        setting = getattr(prefs, "language", "AUTO")
    except (KeyError, AttributeError):
        setting = "AUTO"

    if setting == "AUTO":
        try:
            sys_lang = bpy.context.preferences.view.language
        except (AttributeError, KeyError):
            return "en_US"
        if "zh" in sys_lang.lower() or "chinese" in sys_lang.lower():
            return "zh_CN"
        return "en_US"

    return setting


# [Auto Texture] Get the current language by resolving preferences - Modified: 2026-06-09
def _get_current_language():
    return _resolve_language()


# [Auto Texture] Public API to get the current language - Modified: 2026-06-09
def get_language(context=None):
    return _resolve_language()


# [Auto Texture] Get translated text by key with optional format kwargs - Modified: 2026-06-09
def get_text(key, **kwargs):
    lang = _get_current_language()
    translations = _TRANSLATIONS.get(lang, {})

    text = translations.get(key, None)
    if text is None:
        text = _TRANSLATIONS.get("en_US", {}).get(key, key)

    if kwargs and text:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass

    return text


def register():
    pass


def unregister():
    pass
