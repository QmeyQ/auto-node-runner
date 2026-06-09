# NodeRunner 项目文档

## 1. 项目概述

NodeRunner是一个Blender Python插件，允许用户轻松地**导入和导出着色器节点树及几何节点树**为简单的字符串格式。

### 核心特性
- 将节点树转换为紧凑的可分享字符串
- 从字符串重建节点树
- 支持多种输出格式：Hash(默认)、JSON、AI JSON、XML
- 支持几何节点和着色器节点
- 版本兼容性检查

### 项目信息
- 版本: 1.4.1
- 最低Blender版本: 4.5.0
- 许可证: GPL-3.0-or-later

---

## 2. 项目架构

### 目录结构
```
node_runner-1.4.1/
├── .github/
│   └── workflows/          # CI/CD配置
├── docs/                  # 文档目录
│   ├── css/style.css
│   ├── index.md
│   └── reference.md
├── tests/                 # 测试文件
│   ├── __init__.py
│   ├── helpers.py
│   ├── test_*.py
├── __init__.py            # 插件入口和注册
├── blender_manifest.toml  # Blender扩展清单
├── constants.py           # 全局常量
├── deserialize.py         # 反序列化模块
├── encoding.py            # 编码/解码模块
├── mkdocs.yml             # 文档生成配置
├── node_data.py           # 节点数据表格
├── operators.py           # Blender操作符
└── serialize.py           # 序列化模块
```

### 核心模块依赖关系
```
__init__.py (插件入口)
    ↓
operators.py (UI和操作)
    ↓
    ├── serialize.py (序列化)
    ├── deserialize.py (反序列化)
    └── encoding.py (编解码)
        ↓
    node_data.py (节点数据)
        ↓
    constants.py (常量)
```

---

## 3. 模块详解

### 3.1 插件入口 - `__init__.py`

**功能**: 插件的主入口，负责插件注册和注销。

**核心函数**:
- `register()`: 注册所有操作符和菜单项
- `unregister()`: 注销所有操作符和菜单项

**关键变量**:
- `bl_info`: 包含插件元信息的字典

---

### 3.2 常量模块 - `constants.py`

**功能**: 定义全局使用的常量。

#### 主要常量
| 常量名 | 用途 |
|--------|------|
| `EXPORT_HEADER` | 导出字符串的前缀标识 "__NR" |
| `EXCLUDE_NODE_PROPS` | 不参与序列化的节点属性列表 |
| `SERIALIZE_READONLY_PROPS` | 需要序列化的只读属性 |
| `READONLY_DESERIALIZE_PROPS` | 反序列化时跳过的属性 |
| `MODE_CHANGING_PROPS` | 改变节点模式的属性（必须先设置） |
| `PAIRED_NODE_TYPES` | 配对的区域节点类型 |
| `SOCKET_BASE_TYPES` | 套接字类型映射表 |

---

### 3.3 节点数据模块 - `node_data.py`

**功能**: 提供Blender节点的默认值、套接字名称和属性默认值。

#### 主要变量
- `NODE_DEFAULTS`: 节点类型默认值字典
- `INPUT_NAMES`: 节点输入套接字名称
- `OUTPUT_NAMES`: 节点输出套接字名称

#### 核心函数
| 函数名 | 功能 |
|--------|------|
| `refresh()` | 查询Blender获取当前节点默认值，更新模块级字典 |
| `_introspect_tree()` | 内部函数，检查节点类型并提取数据 |

**重要特性**: 当在Blender中运行时，`refresh()`会在注册时调用，动态构建节点数据表格；否则使用静态后备表(Blender 4.5)。

---

### 3.4 序列化模块 - `serialize.py`

**功能**: 将Blender节点树转换为Python字典。

#### 核心函数
| 函数名 | 功能 |
|--------|------|
| `serialize_node_tree(node_tree, selected_node_names)` | 序列化整个节点树 |
| `serialize_node(node)` | 序列化单个节点 |
| `serialize_attr(node, attr)` | 序列化节点属性 |
| `serialize_*()` | 类型特定的序列化函数 |

#### 序列化数据结构
```python
{
    "nodes": {
        "节点名": {
            "type": "节点类型",
            "label": "标签",
            "location": [x, y],
            "location_absolute": [x, y],
            "inputs": [值1, 值2, ...],
            ... 其他属性
        },
        ...
    },
    "links": [
        {
            "from_node": "源节点名",
            "to_node": "目标节点名",
            "from_socket": "源套接字",
            "to_socket": "目标套接字",
            "from_socket_identifier": "标识符",
            "to_socket_identifier": "标识符",
        },
        ...
    ],
    "name": "节点树名",
    "tree_type": "节点树类型"  # ShaderNodeTree或GeometryNodeTree
}
```

---

### 3.5 反序列化模块 - `deserialize.py`

**功能**: 从Python字典重建Blender节点树。

#### 核心函数
| 函数名 | 功能 |
|--------|------|
| `deserialize_node_tree(node_tree, data, socket_id_map)` | 反序列化整个节点树 |
| `deserialize_node(node_data, node_tree, socket_id_map, defer_io)` | 反序列化单个节点 |
| `deserialize_link(node_map, link_data, socket_id_map)` | 重建节点连接 |
| `deserialize_*()` | 类型特定的反序列化函数 |

#### 关键设计
- **模式属性优先**: `MODE_CHANGING_PROPS`中的属性先设置，影响套接字可用性
- **帧拓扑排序**: 嵌套帧按父节点优先顺序创建
- **配对节点延迟处理**: 区域节点配对后才处理IO
- **绝对位置处理**: 正确计算嵌套节点的相对位置

---

### 3.6 编码模块 - `encoding.py`

**功能**: 处理序列化数据的压缩、编码和解码，支持多种格式。

#### 支持的格式
| 格式 | 常量 | 描述 |
|------|------|------|
| Hash/Base64 | `FORMAT_HASH` | 压缩的base64编码（默认，最紧凑） |
| JSON | `FORMAT_JSON` | 人类可读的JSON |
| AI JSON | `FORMAT_AI_JSON` | 专为AI/对话优化的格式 |
| XML | `FORMAT_XML` | XML格式 |

#### 核心函数
| 函数名 | 功能 |
|--------|------|
| `encode(data)` → str | 压缩并base64编码数据 |
| `decode(base64_encoded)` → dict | 解码并解压base64数据 |
| `encode_as(data, fmt)` → str | 使用指定格式编码 |
| `decode_as(encoded, fmt)` → dict | 使用指定格式解码 |
| `detect_format(data)` → str | 自动检测编码格式 |
| `_compact_data(data)` → dict | 将标准数据转换为紧凑格式 |
| `_expand_data(data)` → dict | 从紧凑格式恢复标准格式 |

#### 紧凑数据结构 (v4)
```python
{
    "v": 4,  # 版本
    "n": [  # 节点数组
        [类型, 名称, 标签, [x, y], 父索引, 属性字典, 输入稀疏数组, (输出稀疏数组)],
        ...
    ],
    "l": [  # 连接数组
        [源索引, 源套接字, 目标索引, 目标套接字],
        ...
    ],
    "t": "节点树类型",  # 可选
    "name": "节点树名",  # 可选
    ...  # 其他元数据
}
```

---

### 3.7 操作符模块 - `operators.py`

**功能**: 定义Blender界面交互和用户操作。

#### 主要操作符
| 操作符 | ID | 功能 |
|--------|----|------|
| 导出到剪贴板 | `node_runner.export_clipboard` | 将选中节点导出为字符串并复制 |
| 导出到文件 | `node_runner.export_file` | 将选中节点导出并保存为文件 |
| 从剪贴板导入 | `node_runner.import_clipboard` | 从剪贴板粘贴节点字符串 |
| 从文件导入 | `node_runner.import_file` | 从文件导入节点 |
| 确认导入 | `node_runner.confirm_import` | Blender版本不匹配时的确认对话框 |

#### 辅助函数
- `_supported_editor_poll(context)`: 检查当前编辑器是否支持
- `_build_export_string(data, export_name, fmt)`: 构建最终导出字符串
- `_do_import(operator, context, raw, ...)`: 执行导入
- `_apply_import(operator, context, data, ...)`: 应用导入的数据
- `_find_modifier_for_tree()`: 查找使用节点树的几何节点修改器
- `_collect_modifier_values()`: 收集修改器值
- `_apply_modifier_values()`: 恢复修改器值

#### 插件偏好
- `import_at_cursor`: 是否在鼠标位置导入
- `select_imported`: 是否选中导入的节点

---

## 4. 工作流程

### 4.1 导出流程
```
用户选择节点
    ↓
调用导出操作符
    ↓
serialize_node_tree() → 字典
    ↓
_compact_data() → 紧凑字典
    ↓
encode() → JSON → zlib压缩 → base64编码
    ↓
加上导出名称和__NR前缀 → 最终字符串
    ↓
复制到剪贴板 / 保存到文件
```

### 4.2 导入流程
```
用户粘贴/选择文件
    ↓
_strip_header_and_detect() → 检测格式
    ↓
decode() → base64解码 → zlib解压 → JSON → 字典
    ↓
_expand_data() → 标准字典
    ↓
检查Blender版本 (不匹配则询问用户)
    ↓
deserialize_node_tree() → 重建节点和连接
    ↓
应用修改器值 (几何节点)
    ↓
完成
```

---

## 5. 关键数据结构

### 5.1 节点数据 (node_data.py)
```python
NODE_DEFAULTS = {
    "节点类型": {
        "inputs": [默认值1, 默认值2, ...],
        "props": {
            "属性名": 默认值,
            ...
        }
    },
    ...
}
```

### 5.2 导出字符串格式
- **Hash格式**: `{名称}__NR{base64编码的压缩数据}`
- **JSON格式**: 完整JSON，包含`export_name`和`blender_version`
- **XML格式**: 完整XML，类型注解在属性中

---

## 6. 使用指南

### 6.1 在Blender中使用
1. 安装插件
2. 在节点编辑器中右键
3. 选择"Node Runner" → 导出/导入选项
4. 按提示操作

### 6.2 编程使用
```python
import bpy
from node_runner.serialize import serialize_node_tree
from node_runner.deserialize import deserialize_node_tree
from node_runner.encoding import encode, decode

# 序列化
tree = bpy.context.active_object.active_material.node_tree
data = serialize_node_tree(tree)
encoded = encode(data)

# 反序列化
decoded = decode(encoded)
socket_id_map = {}
deserialize_node_tree(tree, decoded, socket_id_map)
```

---

## 7. 测试

项目包含完整的测试套件，位于`tests/`目录：
- `test_serialize.py`: 测试序列化
- `test_deserialize.py`: 测试反序列化
- `test_encoding.py`: 测试编码
- `test_encoding_extra.py`: 额外编码测试
- `test_geometry_nodes.py`: 几何节点测试

---

## 8. 开发

### 8.1 文档生成
使用MkDocs生成文档：
```bash
pip install mkdocs mkdocstrings-python mkdocstrings mkdocs-material
mkdocs serve
```

### 8.2 开发环境设置
将仓库克隆到Blender扩展目录，在Blender中启用插件即可。

---

## 9. 扩展与自定义

### 添加新的导出格式
1. 在`constants.py`添加格式常量
2. 在`encoding.py`添加`encode_*`和`decode_*`函数
3. 更新`encode_as()`、`decode_as()`和`detect_format()`
4. 在`operators.py`的`_FORMAT_ITEMS`添加UI选项

### 添加新节点类型支持
- `node_data.py`会在Blender中自动发现
- 如需静态后备表，在`_FALLBACK_DEFAULTS`等添加

---

## 10. 关键设计决策

1. **紧凑格式**: 使用数组代替对象，去除默认值以减小体积
2. **多格式支持**: 满足不同场景需求（分享、存档、AI交互）
3. **动态节点数据**: 在Blender中运行时查询，自动适配版本变化
4. **版本检查**: 避免跨版本不兼容问题
5. **几何节点值保存**: 保存并恢复修改器值，保持最终效果

---

## 11. 参考资料

- 官方网站: https://node-runner.thiering.org
- GitHub: https://github.com/Noah4ever/NodeRunner
- Blender扩展商店: https://extensions.blender.org/add-ons/node-runner/

---

*文档生成时间: 2026-05-25*
