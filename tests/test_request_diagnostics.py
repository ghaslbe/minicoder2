import json

from test_mc import mc, _clean_state, _FakeStreamOpener


def record(capsys):
    return json.loads(capsys.readouterr().out.split('[Request-Diagnose] ')[-1])


def test_request_timings_and_usage(monkeypatch, capsys):
    ticks = iter([10, 12, 17, 20, 24])
    monkeypatch.setattr(mc.time, 'monotonic', lambda: next(ticks))
    d = mc.RequestDiagnostics('model')
    d.event({})
    d.event({'reasoning': 'thinking'})
    d.event({'tool_calls': [{}]})
    d.finish({'prompt_tokens': 100, 'completion_tokens': 50,
              'prompt_tokens_details': {'cached_tokens': 80},
              'completion_tokens_details': {'reasoning_tokens': 30}}, 'stop')
    out = record(capsys)
    assert out['first_data_s'] == 2
    assert out['first_reasoning_s'] == 7
    assert out['first_answer_s'] == 10
    assert out['longest_pause_s'] == 5
    assert out['answer_tokens'] == 20
    assert out['reasoning_tokens'] == 30
    assert out['cache_fraction'] == .8


def test_missing_usage_is_unknown_and_failure_wait_is_measured(monkeypatch, capsys):
    ticks = iter([0, 45])
    monkeypatch.setattr(mc.time, 'monotonic', lambda: next(ticks))
    d = mc.RequestDiagnostics('model')
    d.finish(None, 'network_error_or_stall')
    d.finish(None, 'ignored')
    out = record(capsys)
    assert out['duration_s'] == 45
    assert out['first_data_s'] is None
    assert out['reasoning_tokens'] is None
    assert out['cache_fraction'] is None


def test_real_stream_emits_diagnosis(monkeypatch, capsys):
    lines = [b': keepalive', b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
             b'data: [DONE]']
    monkeypatch.setattr(mc, '_is_local_engine', lambda: True)
    monkeypatch.setattr(mc, 'build_opener', lambda: _FakeStreamOpener(lines, []))
    assert mc._chat_once([], 'model') == ('ok', 'stop')
    assert record(capsys)['status'] == 'stop'
