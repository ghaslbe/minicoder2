import copy
import json

import pytest

from test_mc import mc, _clean_state, _FakeStreamOpener, _historie


def call(name, args, ident="call_1"):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}


def history():
    return [{"role": "system", "content": "system"}, {"role": "user", "content": "task"}]


def assert_pairs(messages):
    pending = set()
    for msg in messages:
        if msg["role"] == "tool":
            assert msg["tool_call_id"] in pending
            pending.remove(msg["tool_call_id"])
        else:
            assert not pending
            pending = {c["id"] for c in msg.get("tool_calls", [])}
    assert not pending


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(mc, "TOOL_MODE", "native")
    monkeypatch.setattr(mc, "CHECK", False)
    monkeypatch.setattr(mc, "ANALYSE", False)
    monkeypatch.setattr(mc, "EXPECTED_FILES", [])
    monkeypatch.setattr(mc, "GIT_ROLLBACK", False)
    monkeypatch.setattr(mc, "RESUME", False)
    monkeypatch.setattr(mc, "MAX_STEPS", 12)
    monkeypatch.setattr(mc, "_is_local_engine", lambda: False)
    monkeypatch.setattr(mc, "_loaded_ctx_tokens", lambda model: 0)


def run_replies(monkeypatch, replies, messages=None):
    replies = iter(replies)
    snapshots = []

    def stream(msgs, model, tools):
        assert_pairs(msgs)
        snapshots.append(copy.deepcopy(msgs))
        return next(replies, mc.NativeReply(calls=[call("finish", {"summary": "done"})]))

    monkeypatch.setattr(mc, "native_chat_stream", stream)
    messages = messages if messages is not None else history()
    result = mc.run_task(messages, "model")
    assert_pairs(messages)
    return result, messages, snapshots


def test_native_stream_assembles_interleaved_calls_and_reasoning(native, monkeypatch):
    deltas = [
        {"reasoning_content": "thinking", "reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "a"}]},
        {"tool_calls": [{"index": 1, "id": "b", "function": {"name": "list_", "arguments": "{"}},
                        {"index": 0, "id": "a", "function": {"name": "read_file", "arguments": '{"path":'}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": '"x.py"}'}},
                        {"index": 1, "function": {"name": "dir", "arguments": "}"}}],
         "reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "b"}]},
    ]
    lines = [("data: " + json.dumps({"choices": [{"delta": d}]})).encode() for d in deltas]
    lines += [b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}', b'data: [DONE]']
    sent = []
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeStreamOpener(lines, sent))
    reply = mc.native_chat_stream(history(), "m", mc._native_tools())
    assert [c["id"] for c in reply.calls] == ["a", "b"]
    assert reply.calls[0]["function"]["arguments"] == '{"path":"x.py"}'
    assert reply.calls[1]["function"]["name"] == "list_dir"
    assert reply.message()["reasoning_content"] == "thinking"
    assert reply.message()["reasoning_details"][0]["text"] == "ab"
    assert sent[0]["tools"] == mc._native_tools()
    assert sent[0]["tool_choice"] == "auto"


@pytest.mark.parametrize("reason", ["length", None, "net_abort"])
def test_native_incomplete_response_regenerates_without_partial_history(native, monkeypatch, reason):
    seen = []
    broken = mc.NativeReply(calls=[call("run", {"command": "must not execute"})])
    good = mc.NativeReply(calls=[call("finish", {"summary": "ok"})])

    def request(msgs, model, **kwargs):
        seen.append(copy.deepcopy(msgs))
        return (broken, reason) if len(seen) == 1 else (good, "tool_calls")

    monkeypatch.setattr(mc, "_chat_once_retry", request)
    assert mc.native_chat_stream(history(), "m", mc._native_tools()) is good
    assert len(seen) == 2
    assert not any(m.get("tool_calls") for m in seen[1])


def test_native_incomplete_response_is_bounded(native, monkeypatch):
    monkeypatch.setattr(mc, "_chat_once_retry", lambda *a, **kw: (mc.NativeReply(), "length"))
    with pytest.raises(SystemExit, match="3 Versuchen"):
        mc.native_chat_stream(history(), "m", mc._native_tools())


@pytest.mark.parametrize("name,args", [
    ("run", {"command": "x", "timeout": True}),
    ("run", {"command": "x", "timeout": 301}),
    ("write_file", {"path": "x", "content": None}),
    ("read_file", {}), ("read_file", {"path": ""}),
    ("finish", {"summary": "x", "action": "run"}),
    ("write_files", {"files": [{"path": "x"}]}),
    ("write_files", {"files": [{"path": "x", "content": ""}] * 4}),
    ("unknown", {}), ("read_file", []),
])
def test_native_arguments_are_validated_before_dispatch(name, args):
    actions = mc._native_actions(mc.NativeReply(calls=[call(name, args)]), mc._native_tools())
    assert "_parse_error" in actions[0][0]


def test_native_valid_empty_content_and_replacement():
    reply = mc.NativeReply(calls=[call("edit_file", {"path": "x", "old": "a", "new": ""})])
    assert mc._native_actions(reply, mc._native_tools())[0][0]["new"] == ""


def test_native_sequential_calls_receive_matching_results(native, monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 2\n")
    result, messages, seen = run_replies(monkeypatch, [mc.NativeReply(calls=[
        call("read_file", {"path": "a.py"}, "a"), call("read_file", {"path": "b.py"}, "b")])])
    assert result == "done"
    outputs = {m["tool_call_id"]: m["content"] for m in messages if m["role"] == "tool"}
    assert "a = 1" in outputs["a"] and "b = 2" in outputs["b"]
    assert len(seen) == 2


def test_native_failure_cancels_remaining_calls(native, monkeypatch, tmp_path):
    _, messages, _ = run_replies(monkeypatch, [mc.NativeReply(calls=[
        call("read_file", {"path": "missing"}, "a"),
        call("write_file", {"path": "must_not_exist", "content": "oops"}, "b")])])
    assert not (tmp_path / "must_not_exist").exists()
    assert "NICHT AUSGEFUEHRT" in next(m["content"] for m in messages if m.get("tool_call_id") == "b")


def test_native_write_keeps_existing_overwrite_gate(native, monkeypatch, tmp_path):
    path = tmp_path / "existing.py"
    path.write_text("important = True\n")
    run_replies(monkeypatch, [mc.NativeReply(calls=[
        call("write_file", {"path": str(path), "content": "lost = True\n"})])])
    assert path.read_text() == "important = True\n"


def test_native_invalid_arguments_do_not_execute(native, monkeypatch, tmp_path):
    run_replies(monkeypatch, [mc.NativeReply(calls=[
        call("write_file", {"path": "bad.py", "content": 123})])])
    assert not (tmp_path / "bad.py").exists()


def test_native_action_fences_are_not_executed(native, monkeypatch, tmp_path):
    run_replies(monkeypatch, [mc.NativeReply(text='```action\n{"action":"write_file","path":"bad","content":"oops"}\n```')])
    assert not (tmp_path / "bad").exists()


def test_native_step_limit_closes_pending_calls(native, monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "MAX_STEPS", 1)
    monkeypatch.setattr(mc, "chat_stream", lambda *a: "handover")
    _, messages, _ = run_replies(monkeypatch, [mc.NativeReply(calls=[
        call("list_dir", {}, "a"), call("write_file", {"path": "bad", "content": "x"}, "b")])])
    assert not (tmp_path / "bad").exists()
    assert messages[-1]["content"] == "handover"


def test_native_finish_is_checked(native, monkeypatch):
    monkeypatch.setattr(mc, "CHECK", True)
    _, messages, _ = run_replies(monkeypatch, [mc.NativeReply(calls=[call("finish", {"summary": "done"})])])
    assert any("FINISH ABGELEHNT" in m.get("content", "") for m in messages)


def test_native_analysis_exposes_read_and_plan_only(native):
    names = {t["function"]["name"] for t in mc._native_tools(analyse=True)}
    assert "plan" in names and "read_file" in names
    assert not names & {"write_file", "run", "finish"}
    assert "```action" not in mc.system_prompt(True, analyse=True)


def test_native_analysis_phase_transition_keeps_valid_history(native, monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "ANALYSE", True)
    (tmp_path / "app.py").write_text("value = 1\n")
    _, messages, seen = run_replies(monkeypatch, [
        mc.NativeReply(calls=[call("read_file", {"path": "app.py"}, "read")]),
        mc.NativeReply(calls=[call("plan", {"punkte": ["app.py: inspect value"]}, "plan")]),
    ])
    assert any("ANALYSE ABGESCHLOSSEN" in m.get("content", "") for m in messages)
    assert "ANALYSIS PHASE" in seen[0][0]["content"]
    assert "ANALYSIS PHASE" not in seen[-1][0]["content"]


def test_native_network_failure_does_not_return_partial_calls(native, monkeypatch):
    class Broken:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"run","arguments":"{}"}}]}}]}'
            raise OSError("connection lost")

    class Opener:
        def open(self, *args, **kwargs):
            return Broken()

    monkeypatch.setattr(mc, "build_opener", lambda: Opener())
    with pytest.raises(mc.NetRetryError):
        mc._chat_once(history(), "m", tools=mc._native_tools())


def test_native_duplicate_ids_are_not_executed(native, monkeypatch):
    reply = mc.NativeReply(calls=[call("list_dir", {}), call("list_dir", {})])
    monkeypatch.setattr(mc, "_chat_once_retry", lambda *a, **kw: (reply, "tool_calls"))
    with pytest.raises(SystemExit):
        mc.native_chat_stream(history(), "m", mc._native_tools())


def test_text_mode_does_not_send_tools(monkeypatch):
    sent = []
    lines = [b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}']
    monkeypatch.setattr(mc, "_is_local_engine", lambda: True)
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeStreamOpener(lines, sent))
    text, _ = mc._chat_once(history(), "m")
    assert text == "ok"
    assert "tools" not in sent[0] and "tool_choice" not in sent[0]


def test_native_prune_replaces_complete_groups_only(native):
    messages = history()
    for i in range(5):
        messages.append(mc.NativeReply(calls=[call("write_file", {"path": f"{i}.py", "content": "x" * 2000}, str(i))]).message())
        messages.append({"role": "tool", "tool_call_id": str(i), "content": "result " + "y" * 1500})
    recent = copy.deepcopy(messages[-4:])
    assert mc.prune_messages(messages, keep=2)
    assert messages[-4:] == recent
    assert_pairs(messages)
    assert sum(bool(m.get("tool_calls")) for m in messages) == 2
    assert "0.py" in messages[2]["content"]


def test_native_resume_preserves_pairs_and_closes_interrupted_call(native, monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "RESUME", True)
    messages = history() + [mc.NativeReply(calls=[call("run", {"command": "x"})]).message()]
    mc._save_transcript(messages)
    restored = mc._load_transcript()
    assert_pairs(restored)
    assert restored[-1]["role"] == "tool"
    assert "unbekannt" in restored[-1]["content"]
    assert restored[-2]["tool_calls"] == messages[-1]["tool_calls"]


def test_native_history_can_be_sent_in_text_mode(monkeypatch):
    monkeypatch.setattr(mc, "_is_local_engine", lambda: True)
    messages = history() + [mc.NativeReply(calls=[call("list_dir", {})]).message(),
                           {"role": "tool", "tool_call_id": "call_1", "content": "files"}]
    before = copy.deepcopy(messages)
    payload = mc._payload_messages(messages)
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in payload)
    assert messages == before


def test_cloud_cache_preserves_prefix_until_budget(monkeypatch):
    monkeypatch.setattr(mc, "_loaded_ctx_tokens", lambda model: 0)
    monkeypatch.setattr(mc, "_is_local_engine", lambda: False)
    monkeypatch.setattr(mc, "CONTEXT_LENGTH", 32768)
    messages = _historie(8)
    before = copy.deepcopy(messages)
    assert not mc.maybe_prune(messages, "m")
    assert messages == before
    monkeypatch.setattr(mc, "CONTEXT_LENGTH", 8000)
    assert mc.maybe_prune(messages, "m")
    after = copy.deepcopy(messages)
    assert not mc.maybe_prune(messages, "m")
    assert messages == after


def test_cloud_pruning_reserves_output_budget(monkeypatch):
    monkeypatch.setattr(mc, "_loaded_ctx_tokens", lambda model: 0)
    monkeypatch.setattr(mc, "_is_local_engine", lambda: False)
    monkeypatch.setattr(mc, "CONTEXT_LENGTH", 20000)
    monkeypatch.setattr(mc, "MAX_TOKENS_PER_CALL", 16000)
    assert mc.maybe_prune(_historie(8), "m")


def test_cloud_zero_budget_keeps_immediate_pruning(monkeypatch):
    monkeypatch.setattr(mc, "_loaded_ctx_tokens", lambda model: 0)
    monkeypatch.setattr(mc, "_is_local_engine", lambda: False)
    monkeypatch.setattr(mc, "CONTEXT_LENGTH", 0)
    assert mc.maybe_prune(_historie(8), "m")


def test_native_size_counts_arguments_and_tool_schemas(native):
    small = mc._history_chars(history())
    big = mc._history_chars(history() + [mc.NativeReply(calls=[call("write_file", {"path": "x", "content": "a" * 10000})]).message()])
    assert small > len(json.dumps(history()))
    assert big > small + 10000


def test_usage_reports_cache_hits(monkeypatch, capsys):
    monkeypatch.setattr(mc, "USAGE", {"prompt": 0, "completion": 0, "cost": 0, "reqs": 0})
    mc.account_usage({"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 80}})
    mc.account_usage({"prompt_tokens": 20, "completion_tokens": 5})
    mc.print_usage_summary()
    assert mc.USAGE["cached"] == 80
    assert mc.USAGE["prompt"] == 120
    assert "Cache: 80" in capsys.readouterr().out


def test_tool_mode_setting_validation(monkeypatch):
    ok, _, changed = mc._apply_setting("tool_mode", "native")
    assert ok and changed and mc.TOOL_MODE == "native"
    assert not mc._apply_setting("tool_mode", "invalid")[0]
