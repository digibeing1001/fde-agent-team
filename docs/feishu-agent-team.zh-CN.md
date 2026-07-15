# 用飞书群承载 FDE 专家团队

飞书可以作为项目办公室的前端，但群聊不是编排器。每个项目使用独立群，`fde-lead` 同时承担秘书和调度官，是唯一的人类入口；它根据任务从专家池提出成员名单，用户确认后才建群或拉人。专家 Bot 只接收明确 `@` 自己且携带 `[FDE_HANDOFF_V1]` JSON 工作包的 Bot 消息。

## 当前能力边界

- 已验证的 lark-cli 最新版为 1.0.70；1.0.69 已具备本方案所需的群、消息和事件能力。
- 数字 `5` 只是单次邀请请求的上限，不是团队人数预设。始终按照已确认的项目名单自动分批：7 个 Bot 必须生成 `[5, 2]` 两批，12 个 Bot 生成 `[5, 5, 2]` 三批；一个群最终最多 15 个 Bot。FDE 的 9 个角色是候选池，不需要全部进入每个项目群。
- Bot 接收“其他 Bot 或用户 @ 当前 Bot”的群消息，需要为应用开通 `im:message.group_at_msg.include_bot:readonly`，订阅 `im.message.receive_v1`。
- 飞书可能重复投递事件。必须用 `message_id` 去重，不能用 `event_id`。

## 配置

1. 首次导入候选池时，使用飞书官方 `registerApp` OAuth 流程批量创建 Bot。每个 Agent 会给出一次在线确认链接；App Secret 只经 stdin 写入 lark-cli profile，不写 inventory 或日志：

   ```bash
   npm --prefix feishu-bootstrap ci --omit=dev --ignore-scripts
   node scripts/feishu-team-bootstrap.mjs --manifest config/feishu-team.example.json
   node scripts/feishu-team-bootstrap.mjs --manifest config/feishu-team.example.json --confirm-create
   ```

   可重复传 `--manifest` 一次导入多个团队。跨团队 Bot 使用 `team_id/agent_id` 作为稳定身份，同名角色不会误合并。重复传 `--only research-agent` 会选择所有团队的同名角色，也可传 `--only fde-team/research-agent` 精确选择；中断后重跑会跳过 inventory 中已完成的 Bot。

   若先建立一个安装引导 Agent，可把授权链接和进度直接发到飞书对话：

   ```bash
   node scripts/feishu-team-bootstrap.mjs \
     --manifest config/feishu-team.example.json \
     --notify-profile fde-installer --notify-chat-id oc_xxx \
     --confirm-create
   ```

   每个独立 Bot 应用首次创建都要分别完成一次在线确认；完成后即可反复加入后续项目群，无需重复确认。`lark-cli auth login` 只在角色确需访问个人日历、云盘等用户数据时使用，不是纯 Bot 建群与消息能力的前置条件。全量预装与“秘书首次接单后只导入缺失角色”共用同一 inventory。
2. 复制 `config/feishu-team.example.json`，为项目设置唯一 `project_id`、群名和环境变量名。
3. 每个 Agent Bot 使用独立 lark-cli profile；运行时可用 `inventory_environment()` 从 inventory 载入非秘密 App ID/Open ID。群 ID 仍按项目隔离，任何 App Secret 都不进入 inventory、聊天或 Git。
4. `fde-lead` 调用 `staffing_proposal()` 形成包含任务目标、核心角色、按需专家和确认令牌的方案。用户确认完全相同的方案后，才把令牌传给 `provision_plan()`。
5. `provision_plan()` 只为已确认成员生成参数数组，且不执行。人工审阅后执行首条建群命令，得到 `chat_id` 写入 `FDE_FEISHU_CHAT_ID`，再执行其余分批拉 Bot 命令。
6. 每个 Bot 分别运行 `lark-cli --profile <profile> event consume im.message.receive_v1 --as bot`，将每行 NDJSON 交给 `route_event()`。

## 路由规则

`route_event()` 会按以下顺序 fail closed：群 ID/项目隔离、明确 @ 当前 Bot、`message_id` 原子去重、发送 Bot 白名单、工作包类型与字段、发件人与收件人一致性、最大 hop、路由边不可重复。人类请求只有秘书 Bot 可以接受。

跨项目不要复用群聊、状态目录或 `project_id`。涉及外部发布、付款、权限和不可逆操作时，仍必须回到现有人工审批门禁；Bot@Bot 不会扩大 Agent 权限。

## 发送交接

`build_handoff()` 返回带目标 Bot Open ID 的 `<at>` 文本和不超过 50 字符的幂等键。发送时使用：

```text
lark-cli --profile <sender> im +messages-send --chat-id <chat_id> --text <text> --idempotency-key <key> --as bot
```

不要让 Bot 对所有群消息自动回答，也不要用自然语言猜测交接对象；这两种做法都会把群聊放大成不可控的回复循环。
