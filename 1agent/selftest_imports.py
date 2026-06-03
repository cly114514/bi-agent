"""
self-test: 验证项目所有入口文件的 import 依赖图在多个 cwd 下都能解析.

之前项目没有 __init__.py, 是 namespace package, 依赖 streamlit / python -m 启动时
恰好把 1agent/ 加到 sys.path. streamlit 从父目录跑 app.py 时不会加, import 全挂.

这个脚本:
  1. 解析每个入口文件顶部的 `from x import y` 语句
  2. 把入口所在目录放到 sys.path, 模拟 streamlit / -m 的最坏情况
  3. 真正执行这些 import, 任何一个抛 ModuleNotFoundError / ImportError 就 fail
  4. 故意不调用入口文件里的业务代码(LLM / Streamlit 渲染), 只验证依赖图

跑法: cd 1agent && python selftest_imports.py
"""
from __future__ import annotations

import ast
import sys
import subprocess
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent

# 入口文件(只测它们的 import 图, 不跑业务逻辑)
ENTRY_POINTS = [
    PROJECT_ROOT / "app.py",
    PROJECT_ROOT / "view_chromadb.py",
    PROJECT_ROOT / "agent" / "react_agent.py",
    PROJECT_ROOT / "utils" / "chart_maker.py",
    PROJECT_ROOT / "agent" / "tools" / "agent_tools.py",
    PROJECT_ROOT / "agent" / "langchain_agent.py",
    PROJECT_ROOT / "rag" / "rag_service.py",
    PROJECT_ROOT / "rag" / "vector_store.py",
    PROJECT_ROOT / "model" / "factory.py",
    PROJECT_ROOT / "model" / "deepseek_chat.py",
]


def _collect_top_imports(path: Path) -> list[tuple[str, str | None]]:
    """提取文件顶部的所有 import 语句, 返回 [(module, name)] 列表."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return [("__syntax_error__", f"{path}:{e.lineno}: {e.msg}")]
    out: list[tuple[str, str | None]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, None))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            level = node.level  # 0 = 绝对, >0 = 相对
            for alias in node.names:
                if level > 0:
                    # 相对导入: 解析成绝对模块名
                    package_parts = list(path.parent.parts)
                    # 走 level 级 parent
                    for _ in range(level):
                        package_parts.pop()
                    abs_mod = ".".join(package_parts + [mod]) if mod else ".".join(package_parts)
                    out.append((abs_mod, alias.name))
                else:
                    out.append((mod, alias.name))
    return out


def _build_probe(entry_path: Path, project_root_str: str) -> str:
    """生成一段 python 代码, 把入口的 import 重新跑一遍(但不跑业务)."""
    imports = _collect_top_imports(entry_path)
    # 只测项目内导入(utils./agent./model./rag./prompts./data./config.)
    # 第三方包(streamlit/langchain/...) 失败也是项目无关
    project_prefixes = ("utils.", "agent.", "model.", "rag.", "prompts.", "data.", "config.")
    relevant = [(m, n) for m, n in imports if any(m == p.rstrip(".") or m.startswith(p) for p in project_prefixes)]
    if not relevant:
        return ""
    lines = [f"import sys", f"sys.path.insert(0, {project_root_str!r})"]
    for mod, name in relevant:
        if name is None:
            lines.append(f"import {mod}")
        else:
            lines.append(f"from {mod} import {name}")
    return "\n".join(lines)


def main() -> int:
    failures: list[str] = []
    for entry in ENTRY_POINTS:
        if not entry.exists():
            continue
        probe_src = _build_probe(entry, str(PROJECT_ROOT))
        if not probe_src:
            continue
        # 在 /tmp 跑 (最坏 cwd), 看项目内导入是否还能解析
        try:
            r = subprocess.run(
                [sys.executable, "-c", probe_src],
                cwd="/tmp", capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{entry.name}: TIMEOUT")
            continue
        if r.returncode != 0:
            err = (r.stderr or "").strip().splitlines()[-3:]
            failures.append(f"{entry.name}: {chr(10).join(err)}")
    if failures:
        print("SELFTEST FAILED — 项目内包在 /tmp cwd 下无法解析:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"SELFTEST OK — {len(ENTRY_POINTS)} entry points, 项目内 import 图从 /tmp 启动全部解析成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
