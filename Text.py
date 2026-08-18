"""AI inference CLI script for texture matching adjustment.

Called by operators.py via subprocess with --data, --prompt, --model arguments.
Outputs streaming response to stdout, followed by final JSON result.

Usage:
    python.exe Text.py --data '{"mat1":{"metal":"path"}}' --prompt "调整提示" --model "/path/to/model.gguf"

Copyright (C) 2026 Auto Texture contributors
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import argparse
import json
import os
import sys
import re

# ---------- 系统提示 ----------
_SYSTEM_PROMPT = (
    "/no_think 你是一个贴图匹配助手。你的任务是根据提供的匹配数据，关闭思考过程。为每个材质重新确定每个贴图类型的正确文件路径。\n"
    "路径使用占位符格式 %目录名%/文件名，其中 %...% 是目录占位符。\n"
    "你只需保留原占位符并替换文件名部分，不要修改占位符本身。\n"
    "执行步骤：\n"
    "1. 对于每个材质，收集其所有贴图类型键（如 basecolor, metallic, roughness, normal 等，不包括 unmatch）。\n"
    "2. 对于每个贴图类型，在当前值和 unmatch 列表中寻找最合适的文件：\n"
    "   - **最优先**：文件名完美与材质名开头（如材质名 'Book'+类型，则选 'Book_BaseColor.png'）。\n"
    "   - **次优先**：文件名包含该材质名且包含贴图类型关键词。\n"
    "   - 如果找到匹配，则使用该路径覆盖原有值；否则保留原有值。但是如果检查出某些不应该使用该文件的项要设置空值\n"
    "3. 最终输出必须：\n"
    "   - 包含所有材质名作为顶层键，不包含 `unmatch` 字段。\n"
    "4. 所有路径必须原样来自输入数据（保持 %目录名%/文件名 格式），不得编造路径或更改占位符。\n"
    "5. 输出必须是合法的 JSON，不得包含任何解释或额外文本。"
)


def _build_user_message(data, prompt):
    msg = f"匹配数据:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n"
    if prompt:
        msg += f"\n附加提示词:\n{prompt}\n"
    msg += (
        "\n路径格式说明：路径形如 %C%/Book_BaseColor.png 或 %初始工程&贴图%/Book_BaseColor.png。\n"
        "%...% 是目录占位符，必须原样保留，不要修改占位符，只需为每个贴图类型选择最匹配的 %目录名%/文件名 路径。\n"
        "输出格式示例：\n"
        '{"Book": {"basecolor": "%C%/Book_BaseColor.png", "metallic": "%C%/Book_Metallic.png", ...}}\n'
        "注意：输出中不得包含 unmatch，不得包含解释文本。 /no_think"
    )
    return msg


def _extract_json(raw):
    """从可能包含解释文本的响应中提取第一个完整的 JSON 对象。"""
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description="AI texture match adjustment")
    parser.add_argument("--config_file", default="", help="JSON config file with all arguments")
    parser.add_argument("--data", default="", help="JSON string of match data")
    parser.add_argument("--prompt", default="", help="Additional prompt text")
    parser.add_argument("--model", default="", help="Absolute path to GGUF model file")
    parser.add_argument("--history", default="", help="JSON string of conversation history")
    parser.add_argument("--no_think", action="store_true", help="Prepend /no_think to system prompt")
    parser.add_argument("--system_prompt", default="", help="Custom system prompt override")
    parser.add_argument("--full_response", action="store_true", help="Output full response text instead of extracted JSON")
    parser.add_argument("--n_ctx", type=int, default=40960, help="Context window size")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature for sampling")
    args = parser.parse_args()

    if args.config_file:
        with open(args.config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        args.data = cfg.get("data", "")
        args.prompt = cfg.get("prompt", "")
        args.model = cfg.get("model", "")
        args.history = cfg.get("history", "")
        args.no_think = cfg.get("no_think", False)
        args.system_prompt = cfg.get("system_prompt", "")
        args.full_response = cfg.get("full_response", False)
        args.n_ctx = int(cfg.get("n_ctx", 40960))
        args.temperature = float(cfg.get("temperature", 0.0))

    if not args.data or not args.model:
        print(json.dumps({"error": "Missing required arguments: data and model"}), flush=True)
        sys.exit(1)

    if args.system_prompt or args.full_response:
        user_content = args.data
        if args.prompt:
            user_content += f"\n\n用户指令: {args.prompt}"
    else:
        try:
            submission_data = json.loads(args.data)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid input data: {e}"}), flush=True)
            sys.exit(1)
        user_content = _build_user_message(submission_data, args.prompt)

    try:
        from llama_cpp import Llama

        if not os.path.isfile(args.model):
            print(json.dumps({"error": f"Model file not found: {args.model}"}), flush=True)
            sys.exit(1)

        llm = Llama(
            model_path=args.model,
            n_gpu_layers=-1,
            n_ctx=args.n_ctx,
            temperature=args.temperature,
            n_batch=512,
            verbose=False
        )

        if args.system_prompt:
            sys_prompt = args.system_prompt
        else:
            sys_prompt = _SYSTEM_PROMPT
        if args.no_think and not sys_prompt.startswith("/no_think"):
            sys_prompt = "/no_think " + sys_prompt

        messages = [{"role": "system", "content": sys_prompt}]

        if args.history:
            try:
                hist = json.loads(args.history)
                if isinstance(hist, list):
                    messages.extend(hist)
            except json.JSONDecodeError:
                pass

        messages.append({"role": "user", "content": user_content})

        full_response = ""
        stream = llm.create_chat_completion(messages=messages, stream=True, max_tokens=40960)
        for chunk in stream:
            delta = chunk["choices"][0]["delta"].get("content", "")
            if delta:
                full_response += delta
                print(delta, end="", file=sys.stderr, flush=True)

        if args.full_response:
            print(full_response, flush=True)
        else:
            clean_json = _extract_json(full_response)
            if clean_json is None:
                print(json.dumps({"error": "Failed to parse JSON from model output", "raw": full_response}, ensure_ascii=False), flush=True)
                sys.exit(1)
            print(json.dumps(clean_json, ensure_ascii=False), flush=True)

    except Exception as e:
        print(json.dumps({"error": str(e)}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()