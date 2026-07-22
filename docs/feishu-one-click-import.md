# 飞书 CLI 一键导入与项目启动

## 身份模型

飞书中只安装一个应用机器人：`fde-lead` 团队统筹官（秘书）。这不代表其他角色被合并进秘书：秘书会为项目创建多个真正独立的 Agent 实例，每个实例拥有自己的 ID、收件箱、工作状态和产出流。

受“飞书中仅出现一个机器人”的约束，独立 Agent 不会各自注册成飞书应用成员。项目群会展示完整 Agent 名册；各 Agent 的消息由统筹官机器人转发，并同时显示角色名与不可混淆的 `agent_instance_id`。因此群内沟通入口只有一个，执行主体仍然是多个独立 Agent。

## 前置条件

1. 安装官方飞书 CLI：`npx @larksuite/cli@latest install`
2. 初始化并登录：`lark-cli config init --new`、`lark-cli auth login --recommend`
3. 机器人身份至少需要创建群和发送消息权限；用户身份需要向群添加成员的权限。
4. 复制并调整 `config/feishu-team.example.json`。不要配置 `worker_bot_app_ids`。

## 一条命令完成导入与启动

```powershell
python -m adapters.feishu.team_cli bootstrap `
  --config config/feishu-team.example.json `
  --project-id fde-customer-onboarding `
  --name "FDE｜客户入驻提效" `
  --owner-open-id ou_owner `
  --member-open-id ou_sponsor
```

该命令会依次完成：

1. 验证 `lark-cli` 登录态并导入团队清单；
2. 由统筹官机器人创建私有项目群；
3. 将用户及指定的人类成员加入项目群；
4. 为所有角色创建相互独立的 Agent 实例并绑定项目群；
5. 发布团队名册；
6. 只提出一个当前最关键的问题；
7. 上下文齐备后自动激活全部 Agent 并开始任务拆解与工作。

如果上下文已经由上游系统提供，可以通过 `--context-json` 一次传入以下字段并立即启动：`business_outcome`、`users_and_workflow`、`success_measure`、`constraints`、`available_evidence`。项目类型必须是 `fde_ai_consulting`。

## 逐轮回答

```powershell
python -m adapters.feishu.team_cli answer `
  --project-id fde-customer-onboarding `
  --field business_outcome `
  --text "把客户从签约到首次上线的周期缩短 30%"
```

系统只接受当前待回答字段。每次回答后最多发布一个新问题；所有必需上下文齐备后，不再追问并自动激活团队。

## 分步调用

也可以先执行 `import-team --config ...`，后续每个项目单独执行 `start-project ...`。全局参数 `--state` 默认仍接受 `.fde/feishu-team-state.json`，实际数据会以原子化的逐项目快照写入同名 `.d` 目录；若检测到旧版单文件状态，会自动完成一次迁移。

## 安全边界

- 默认不创建或安装任何工作 Agent 机器人，确保飞书只有一个统筹官机器人。
- 额外群成员通过用户身份加入，失败 ID 会让启动中止，避免“名义上全员已入群”。
- 非 FDE AI 咨询项目会被入口门控拒绝。
- 所有 Agent 发言保留角色与实例签名；秘书不能把自己的产出伪装成其他角色。
