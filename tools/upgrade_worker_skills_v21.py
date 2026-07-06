"""
FDE Agent Team - 8 个 worker agent SKILL.md v2.1 批量升级脚本

升级内容（统一模式）:
  1. version: "2.0" -> "2.1"
  2. frontmatter 增加 user_constraints_handling / platform_abstractions / v21_changes 字段
  3. 文档末尾追加 v2.1 升级说明章节

用法:
  python tools/upgrade_worker_skills_v21.py
"""

import re
from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "agents"

WORKER_AGENTS = [
    "echo-agent",
    "delta-agent",
    "productize-agent",
    "research-agent",
    "knowledge-curator",
    "qa-agent",
    "legal-agent",
    "coach-agent",
]

# v2.1 新增的 frontmatter 字段（插入到 compatibility: 行之前）
V21_FRONTMATTER_BLOCK = """  user_constraints_handling:
    receives: "通过 call_*_agent 工具的 user_constraints 参数接收（由适配器代码层自动注入到 system prompt 末尾）"
    must_follow: true
    must_report: "输出中必须包含 constraints_followed 字段，列出已遵循的约束清单"
    failure_mode: "违反用户约束 = 任务失败，QA Agent 在 user_constraint_compliance 维度审查"
  platform_abstractions:
    file_storage: "adapters/base.py FileStorage 接口（飞书用 FeishuStorage/lark-cli，LangGraph 用 LangGraphStorage）"
    message_bus: "adapters/base.py MessageBus 接口"
    state_store: "adapters/base.py StateStore 接口"
  v21_changes:
    - "version 升级为 2.1"
    - "约束注入由适配器代码层执行（非 LLM 自觉），见 team.yaml constraints_persistence.executor"
    - "输出必须包含 constraints_followed 字段（供 QA Agent user_constraint_compliance 审查）"
    - "lark-cli 命令通过 FileStorage/MessageBus/StateStore 抽象接口调用（平台无关）"
    - "状态由 fde-lead 通过 state_machine.json 管理，本 agent 是被调用的 worker node"
    - "调用入口: fde-lead 通过 call_<agent_id>_agent 工具委派（function calling）"
"""

# v2.1 末尾追加的章节
V21_TAIL_SECTION = """

---

## v2.1 升级说明（2026-07-06）

本 SKILL.md 已升级到 v2.1，与 [team.yaml](../../team.yaml) v2.1.0 配合使用。

### 与 v2.0 的差异

| 维度 | v2.0 | v2.1 |
|------|------|------|
| **约束传递** | 依赖 LLM 自觉传递 | 适配器代码层自动注入到 system prompt 末尾（硬性保证） |
| **约束遵循报告** | 无 | 输出必须含 `constraints_followed` 字段 |
| **平台依赖** | 硬编码 `lark-cli` | 通过 FileStorage/MessageBus/StateStore 抽象接口 |
| **调用方式** | 关键词路由 | function calling tool（`call_<agent_id>_agent`） |
| **状态管理** | prompt 描述 | state_machine.json 程序化状态机 |
| **QA 审查** | 6 维度 | 7 维度（新增 `user_constraint_compliance`） |

### 约束遵循机制（v2.1 P3 修正）

本 agent 收到的 system prompt 末尾会包含 `[强制用户约束 - 不可违反]` 块，由适配器代码层自动注入（非 LLM 自觉）。约束来源是工作包的 `user_constraints` 字段。

**输出要求**：在返回结果中必须包含 `constraints_followed` 数组，列出本次执行中遵循的约束清单。例如：

```json
{
  "constraints_followed": [
    "language: zh-CN",
    "knowledge_base: getnote:Q0GpeEvJ",
    "min_sources_per_fact: 2"
  ]
}
```

QA Agent 会在 `user_constraint_compliance` 维度审查本 agent 是否真的遵循了约束。

### 平台无关性

本 SKILL.md 不嵌入任何平台特定配置。所有平台特定值（飞书 topic_id、folder_token 等）在 `config/platform.json` 中。所有平台 API 调用通过 `adapters/base.py` 的三大接口抽象。

### 调用入口

FDE Lead 通过 function calling tool `call_<agent_id>_agent` 委派任务，参数定义见 `config/tools.schema.json`。本 agent 不主动启动，只响应 FDE Lead 的调用。

### 向后兼容

v2.1 保持对 Hermes/OpenClaw 的向后兼容：
- SKILL.md 仍可作为 Hermes/OpenClaw 的 entrypoint
- `lark-cli` 命令保留在 `adapters/feishu/` 中
- C 类平台配合 `adapters/state_guard.py` 使用
"""


def upgrade_skill_md(file_path: Path) -> str:
    """升级单个 SKILL.md，返回升级状态描述"""
    content = file_path.read_text(encoding="utf-8")

    # 1. 检查是否已是 v2.1
    if 'version: "2.1"' in content:
        return f"[SKIP] {file_path.parent.name} 已是 v2.1"

    # 2. 升级 version
    new_content = re.sub(
        r'version:\s*"2\.0"',
        'version: "2.1"',
        content,
        count=1,
    )
    if new_content == content:
        return f"[WARN] {file_path.parent.name} 未找到 version: \"2.0\"，跳过 version 升级"

    # 3. 在 compatibility: 行之前插入 v2.1 字段
    # 注意：compatibility 是顶层字段（无缩进），metadata 的子字段有缩进
    # 我们需要把 v21 字段插入到 metadata 块的末尾（即 compatibility: 之前）
    # 但 v21 字段应该作为 metadata 的子字段（有 2 空格缩进）
    if "compatibility:" in new_content:
        new_content = new_content.replace(
            "compatibility:",
            V21_FRONTMATTER_BLOCK + "compatibility:",
            1,
        )
    else:
        # 如果没有 compatibility 字段，插到 --- 之前
        # 找到第二个 ---
        parts = new_content.split("---", 2)
        if len(parts) >= 3:
            new_content = parts[0] + "---" + parts[1] + V21_FRONTMATTER_BLOCK + "---" + parts[2]

    # 4. 在文件末尾追加 v2.1 说明章节（如果还没有）
    if "## v2.1 升级说明" not in new_content:
        new_content = new_content.rstrip() + V21_TAIL_SECTION

    file_path.write_text(new_content, encoding="utf-8")
    return f"[OK]   {file_path.parent.name} 升级到 v2.1"


def main():
    print("=" * 60)
    print("FDE Agent Team - Worker Agent SKILL.md v2.1 批量升级")
    print("=" * 60)
    for agent_name in WORKER_AGENTS:
        skill_path = AGENTS_DIR / agent_name / "SKILL.md"
        if not skill_path.exists():
            print(f"[MISS] {agent_name} SKILL.md 不存在")
            continue
        result = upgrade_skill_md(skill_path)
        print(result)
    print("=" * 60)
    print("升级完成。请运行 verify_upgrade.py 验证。")


if __name__ == "__main__":
    main()
