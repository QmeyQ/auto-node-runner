# Auto Node Runner (AI)

所有开发均由AI实现，该项目属于个人学习项目，不涉及任何商业用途，所有内容均不保留任何权利和义务。

llama 版本匹配

3. 安装 64 位的 CUDA 版本（根据您的 CUDA 版本）
首先确认 CUDA 版本
打开命令提示符（不是 Blender 的），运行：

cmd
nvidia-smi
在顶部找到 CUDA Version: X.Y，例如 12.1 或 11.8。

然后使用对应的命令安装 64 位 wheel：
如果 CUDA 版本 ≥ 12.0（例如 12.1）：

cmd
.\python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 --only-binary :all:
如果 CUDA 版本为 11.x（例如 11.8）：

cmd
.\python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu118 --only-binary :all:
如果不确定 CUDA 版本，或没有 NVIDIA 显卡，可以安装 CPU 版本（也能用，只是推理较慢）：

cmd
.\python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --only-binary :all:
⚠️ 务必带上 --only-binary :all:，防止 pip 尝试从源码编译（32/64 位自动匹配）。

4. 验证安装
cmd
.\python -c "from llama_cpp import Llama; print('OK')"
如果无报错，说明 64 位 DLL 已成功加载。


# NodeRunner

#### Example of exporting and importing shader nodes


https://github.com/user-attachments/assets/ea813ab1-0408-41d4-8273-a8ba4953d2e2


## Website to Share & Discover node trees 

Visit

https://node-runner.thiering.org

## Overview

This **Blender Python Addon** allows you to easily **import and export shader nodes** to a simple **string format**. The exported strings can be easily shared through text messengers, YouTube comments, or any other text-based platform, allowing you to quickly share node setups without the need to send actual files.

With this addon, sharing and reusing shader node setups becomes as simple as copying and pasting a line of text!

# Installation

Install it from the official blender extension store:
https://extensions.blender.org/add-ons/node-runner/

## Features

- **🚀 Export Shader Nodes**: Converts your current shader node setup into a compact string that can be copied and shared.
- **🔄 Import Shader Nodes**: Paste a shared shader node string to recreate the exact node setup in your own Blender project.
- **💬 Text-Based Sharing**: Exported node strings are lightweight and can be shared via messengers, social media, or even in YouTube comments.

## Development

1. Clones this repository into your `blender/BLENDER_VERSION/extensions/user_default/` folder (should be at your local blender installation).
2. Open Blender and go into "`Edit > Preferences > Add-Ons`" and search for `Node Runner`.
3. Check the checkbox.

### Documentation

To create documentation you need to have installed:

```bash
pip install mkdocs mkdocstrings-python mkdocstrings mkdocs-material
```

After that you can run the following command:

```bash
mkdocs serve
```

## Example

- **Export**: Right-click in the shader editor > **Node Runner Export** > Copy the generated string.
- **Import**: Right-click in the shader editor > **Node Runner Import** > Paste the node string > Nodes are recreated.

## License

This addon is released under the [MIT License](LICENSE).

---

Feel free to report any issues or contribute to the project. Happy blending and node sharing! 🔥
