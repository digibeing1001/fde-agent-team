#!/usr/bin/env python3
import asyncio
import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.durable_state_store import AtomicJsonStateStore
from adapters.feishu.feishu_adapter import FeishuMessageBus
from adapters.feishu.team_gateway import (
    GatewayError, build_handoff, invite_batch_sizes, provision_plan, route_event,
    staffing_proposal, validate_manifest,
)


MANIFEST = json.loads((ROOT / "config" / "feishu-team.example.json").read_text(encoding="utf-8"))


def env_for(manifest):
    env = {manifest["chat_id_env"]: "oc_fde"}
    for index, agent in enumerate(manifest["agents"]):
        env[agent["app_id_env"]] = f"cli_{index}"
        env[agent["open_id_env"]] = f"ou_{index}"
    return env


def test_manifest_and_seven_bot_provision_uses_five_plus_two_batches():
    validate_manifest(MANIFEST)
    proposal = staffing_proposal(
        MANIFEST,
        objective="Run a seven-bot FDE project team",
        specialists=[
            "echo-agent", "delta-agent", "productize-agent", "research-agent",
            "knowledge-curator", "qa-agent",
        ],
    )
    assert proposal["core_agents"] == ["fde-lead"]
    assert len(proposal["selected_agents"]) == 7
    assert invite_batch_sizes(len(proposal["selected_agents"])) == [5, 2]
    commands = provision_plan(
        MANIFEST, env_for(MANIFEST), proposal=proposal,
        confirmation_token=proposal["confirmation_token"],
    )
    assert len(commands) == 2
    assert commands[0][3:5] == ["im", "+chat-create"]
    assert "--dry-run" not in commands[0]  # plan is argv only and never executes
    first_batch = commands[0][commands[0].index("--bots") + 1].split(",")
    second_batch = json.loads(commands[1][commands[1].index("--data") + 1])["id_list"]
    assert len(first_batch) == 5
    assert len(second_batch) == 2
    assert first_batch + second_batch == ["cli_0", "cli_1", "cli_2", "cli_3", "cli_4", "cli_5", "cli_6"]
    try:
        provision_plan(MANIFEST, env_for(MANIFEST), proposal=proposal, confirmation_token="wrong")
        raise AssertionError("unconfirmed staffing must fail closed")
    except GatewayError:
        pass


def test_human_entry_bot_handoff_dedup_and_loop_guard():
    env = env_for(MANIFEST)
    secretary_open = env[MANIFEST["agents"][0]["open_id_env"]]
    worker_open = env[MANIFEST["agents"][1]["open_id_env"]]
    with tempfile.TemporaryDirectory() as tmp:
        store = AtomicJsonStateStore(tmp)
        human = {
            "chat_id": "oc_fde", "message_id": "om_human", "sender_type": "user",
            "sender_id": "ou_human", "mentions": [{"id": secretary_open}], "content": "做一个 FDE 项目",
        }
        assert route_event(MANIFEST, human, current_agent_id="fde-lead", store=store, env=env).accepted
        assert route_event(MANIFEST, human, current_agent_id="fde-lead", store=store, env=env).reason == "duplicate_message"

        text, key = build_handoff(
            MANIFEST, sender="fde-lead", target="echo-agent", task="澄清需求",
            correlation_id="corr-1", env=env,
        )
        assert len(key) == 40 and HANDOFF_MARKER in text
        bot = {
            "chat_id": "oc_fde", "message_id": "om_bot", "sender_type": "bot",
            "sender_id": secretary_open, "mentions": [{"id": worker_open}], "content": text,
        }
        decision = route_event(MANIFEST, bot, current_agent_id="echo-agent", store=store, env=env)
        assert decision.accepted and decision.reason == "bot_handoff"
        wrong_chat = dict(bot, message_id="om_wrong", chat_id="oc_other")
        assert not route_event(MANIFEST, wrong_chat, current_agent_id="echo-agent", store=store, env=env).accepted


def test_message_bus_uses_current_cli_shortcuts():
    calls = []
    def runner(args, input_data=None):
        calls.append((args, input_data))
        return json.dumps({"messages": []})
    bus = FeishuMessageBus("oc_fde", profile="fde-lead", runner=runner)
    asyncio.run(bus.send("oc_fde", "hello"))
    asyncio.run(bus.poll())
    asyncio.run(bus.ack("om_1"))
    assert calls[0][0][:4] == ["--profile", "fde-lead", "im", "+messages-send"]
    assert calls[1][0][3] == "+chat-messages-list"
    assert len(calls) == 2


HANDOFF_MARKER = "[FDE_HANDOFF_V1]"

if __name__ == "__main__":
    test_manifest_and_seven_bot_provision_uses_five_plus_two_batches()
    test_human_entry_bot_handoff_dedup_and_loop_guard()
    test_message_bus_uses_current_cli_shortcuts()
    print("p10 feishu team tests: PASS")
