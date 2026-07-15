"""
FDE Agent Team - 飞书适配器（v2.1 P5 实现）

实现三大接口的飞书版本：
  - FeishuStorage:    通过 lark-cli drive/docx 操作飞书云空间文件
  - FeishuMessageBus: 通过 lark-cli im 发送/接收消息
  - FeishuStateStore: 飞书文档作为 KV 存储

设计依据:
  - v2.0 向后兼容：保留 lark-cli 命令调用
  - v2.1 P3 修正：约束注入由适配器代码层执行（非 LLM 自觉）
  - v2.1 P4 修正：配合 state_guard.py 拦截非法状态转换

依赖:
  - lark-cli（已在用户环境中安装并完成 auth login）
  - config/platform.json 中的 feishu 配置（topic_id、folder_token 等）

使用方式:
  from adapters.feishu.feishu_adapter import FeishuStorage, FeishuMessageBus, FeishuStateStore

  storage = FeishuStorage(folder_token=...)
  msg_bus = FeishuMessageBus(chat_id=...)
  state = FeishuStateStore(state_doc_token=...)
"""

import hashlib
import json
import subprocess
import tempfile
import os
from typing import Any, Callable, Optional

from adapters.base import FileStorage, MessageBus, StateStore


def _run_lark_cli(args: list, input_data: Optional[str] = None) -> str:
    """
    执行 lark-cli 命令并返回 stdout。

    Args:
        args: lark-cli 参数列表，如 ["drive", "upload", "--path", "/foo"]
        input_data: 标准输入数据（可选）

    Returns:
        stdout 输出（字符串）

    Raises:
        RuntimeError: 如果 lark-cli 返回非零退出码
    """
    cmd = ["lark-cli"] + args
    result = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"lark-cli {' '.join(args)} failed (exit={result.returncode}): {result.stderr}"
        )
    return result.stdout


class FeishuStorage(FileStorage):
    """飞书云空间文件存储"""

    def __init__(self, folder_token: Optional[str] = None):
        """
        Args:
            folder_token: 飞书云空间根目录 token（从 config/platform.json 读取）
        """
        self.folder_token = folder_token

    async def read(self, path: str) -> str:
        # path 格式: "docx/<token>" 或 "/folder/subfolder/file.md"
        # lark-cli docx read --token <token>
        if path.startswith("docx/"):
            token = path[5:]
            return _run_lark_cli(["docx", "read", "--token", token])
        # 简化实现：直接读 docx token
        return _run_lark_cli(["docx", "read", "--token", path])

    async def write(self, path: str, content: str) -> None:
        # 写入飞书文档：先写临时文件，再上传
        # 实际实现需根据 path 判断是创建新文档还是更新已有文档
        if path.startswith("docx/"):
            token = path[5:]
            # 更新已有文档
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
                f.write(content)
                tmp_path = f.name
            try:
                _run_lark_cli(["docx", "write", "--token", token, "--file", tmp_path])
            finally:
                os.unlink(tmp_path)
        else:
            # 创建新文档
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
                f.write(content)
                tmp_path = f.name
            try:
                _run_lark_cli([
                    "drive", "upload",
                    "--file", tmp_path,
                    "--parent", self.folder_token or "",
                    "--name", os.path.basename(path),
                ])
            finally:
                os.unlink(tmp_path)

    async def mkdir(self, parent: str, name: str) -> str:
        # lark-cli drive mkdir --parent <token> --name <name>
        out = _run_lark_cli(["drive", "mkdir", "--parent", parent, "--name", name])
        # 解析返回的 folder_token
        try:
            data = json.loads(out)
            return data.get("token", data.get("folder_token", ""))
        except json.JSONDecodeError:
            return out.strip()

    async def list(self, path: str) -> list:
        out = _run_lark_cli(["drive", "list", "--parent", path])
        try:
            data = json.loads(out)
            return data if isinstance(data, list) else data.get("files", [])
        except json.JSONDecodeError:
            return []

    async def exists(self, path: str) -> bool:
        try:
            await self.read(path)
            return True
        except RuntimeError:
            return False


class FeishuMessageBus(MessageBus):
    """飞书即时通讯消息总线"""

    def __init__(
        self,
        chat_id: Optional[str] = None,
        *,
        profile: Optional[str] = None,
        runner: Callable[[list, Optional[str]], str] = _run_lark_cli,
    ):
        """
        Args:
            chat_id: 默认群聊 ID（从 config/platform.json 读取）
        """
        self.chat_id = chat_id
        self.profile = profile
        self._runner = runner

    def _run(self, args: list, input_data: Optional[str] = None) -> str:
        prefix = ["--profile", self.profile] if self.profile else []
        return self._runner(prefix + args, input_data)

    async def send(self, target: str, message: str, msg_type: str = "notification") -> None:
        # 使用 CLI 1.0.69+ 的稳定 shortcut。幂等键避免事件重投导致重复发送。
        digest = hashlib.sha256(f"{target}\0{message}".encode("utf-8")).hexdigest()[:40]
        self._run([
            "im", "+messages-send", "--chat-id", target,
            "--text", message, "--idempotency-key", digest, "--as", "bot",
        ])

    async def poll(self, filter_dict: Optional[dict] = None) -> list:
        # 历史补偿读取；实时接收应使用 `lark-cli event consume im.message.receive_v1`。
        chat = (filter_dict or {}).get("to", self.chat_id)
        if not chat:
            return []
        out = self._run([
            "im", "+chat-messages-list", "--chat-id", chat,
            "--page-size", "50", "--order", "desc", "--no-reactions", "--as", "bot",
        ])
        try:
            data = json.loads(out)
            return data if isinstance(data, list) else data.get("messages", [])
        except json.JSONDecodeError:
            return []

    async def ack(self, message_id: str) -> None:
        # 飞书事件没有消费 ack API。处理确认由 team_gateway 基于 message_id
        # 写入本地 AtomicJsonStateStore，不能调用不存在的 "im read" 命令。
        return None


class FeishuStateStore(StateStore):
    """
    飞书文档作为 KV 状态存储。

    设计：
      - 一个项目一个飞书文档（state_doc_token）
      - 文档内容是 JSON：{ "project_id": "...", "current_state": "...", ... }
      - 每次 set 重写整个文档（简化实现，单项目并发低）
      - 多项目时用多个文档或一个文档中按 project_id 分区
    """

    def __init__(self, state_doc_token: Optional[str] = None):
        """
        Args:
            state_doc_token: 存储状态的飞书文档 token
        """
        self.state_doc_token = state_doc_token
        self._cache: dict = {}  # 本地缓存，减少 lark-cli 调用

    def _load_all(self) -> dict:
        """加载整个状态文档"""
        if not self.state_doc_token:
            return self._cache
        if self._cache:
            return self._cache
        try:
            out = _run_lark_cli(["docx", "read", "--token", self.state_doc_token])
            self._cache = json.loads(out) if out.strip() else {}
        except (RuntimeError, json.JSONDecodeError):
            self._cache = {}
        return self._cache

    def _save_all(self) -> None:
        """保存整个状态文档"""
        if not self.state_doc_token:
            return
        content = json.dumps(self._cache, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name
        try:
            _run_lark_cli(["docx", "write", "--token", self.state_doc_token, "--file", tmp_path])
        finally:
            os.unlink(tmp_path)

    def get(self, project_id: str, key: str) -> Any:
        data = self._load_all()
        return data.get(project_id, {}).get(key)

    def set(self, project_id: str, key: str, value: Any) -> None:
        data = self._load_all()
        data.setdefault(project_id, {})[key] = value
        self._save_all()

    def delete(self, project_id: str, key: str) -> None:
        data = self._load_all()
        if project_id in data and key in data[project_id]:
            del data[project_id][key]
            self._save_all()

    def keys(self, project_id: str) -> list:
        data = self._load_all()
        return list(data.get(project_id, {}).keys())


class FeishuAdapter:
    """
    飞书平台完整适配器：组合三大接口 + StateGuard + 约束注入。

    使用方式:
      from adapters.feishu.feishu_adapter import FeishuAdapter
      from adapters.state_guard import StateGuard

      adapter = FeishuAdapter(platform_config)
      guard = StateGuard(state_machine_path, adapter.state_store)

      # 包装 LLM 调用
      def call_fde_lead(user_msg):
          response = llm.invoke(user_msg)
          # StateGuard 自动拦截状态转换
          return guard.wrap_llm_call(lambda: response, project_id)
    """

    def __init__(self, platform_config: dict):
        """
        Args:
            platform_config: 从 config/platform.json 加载的飞书配置
                {
                  "feishu": {
                    "folder_token": "...",
                    "chat_id": "...",
                    "state_doc_token": "..."
                  }
                }
        """
        feishu_cfg = platform_config.get("feishu", {})
        self.storage = FeishuStorage(folder_token=feishu_cfg.get("folder_token"))
        self.message_bus = FeishuMessageBus(
            chat_id=feishu_cfg.get("chat_id"),
            profile=feishu_cfg.get("profile"),
        )
        self.state_store = FeishuStateStore(state_doc_token=feishu_cfg.get("state_doc_token"))

    def call_worker_agent(
        self,
        tool_name: str,
        tool_args: dict,
        work_package_constraints: dict,
        role_card_loader,
        invoke_sub_agent_fn,
    ):
        """
        拦截 call_* 工具调用，自动注入用户约束（v2.1 P3 修正）。

        飞书平台的子 agent 调用通常通过 lark-cli 发消息给对应 agent bot，
        这里复用基类的 call_worker_agent_wrapper 逻辑。
        """
        from adapters.base import call_worker_agent_wrapper
        return call_worker_agent_wrapper(
            tool_name=tool_name,
            tool_args=tool_args,
            work_package_constraints=work_package_constraints,
            role_card_loader=role_card_loader,
            invoke_sub_agent_fn=invoke_sub_agent_fn,
        )
