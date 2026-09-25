# OpenCode CRUD comparison, 2026-09-25

## Setup and result

- OpenCode 1.18.23, `run --pure --auto --format json`.
- Model: `z-ai/glm-5.3-flash`, exclusively through the local logging proxy.
- Task: unchanged body of `mc_skills/crud-personen.md`, without `$ARGUMENTS`.
- Fresh temporary project; no changes to `mc.py` or global OpenCode settings.
- Successful exit, 6 agent steps, 8 tool calls, 45.5 seconds between first
  step-start and final step-finish. There were 7 HTTP requests, including a
  separate title request. All returned 200, with no logged proxy errors.
- Three files written in one model response, then server startup, curl checks,
  cleanup and a final answer. No edit/retry cycle was needed in this run.
- Independently checked with Flask's test client and an isolated SQLite DB:
  root page, create, list, single read, update, delete, missing ID and missing
  name all passed. Browser interactions were not independently tested.
- Negative test: POST with `{"name":123}` returns 500. The model's claim of
  complete testing is broader than its actual checks.

Local artifacts (temporary, not committed):
`/var/folders/kf/lgpt9l8s4hv4q1vv67ccq3dm0000gn/T/m2coder-opencode-crud-t2xtm0pf/`
contains `project/`, `events.jsonl`, `stderr.log`, and redacted `proxy-logs/`.

## Candidates for mc.py

1. **Cache-aware cloud history pruning.** The final request reused 15,360 of
   15,770 prompt tokens. `maybe_prune()` already preserves stable history for
   locally discoverable context windows, but falls back to pruning each step
   when that discovery fails, including cloud endpoints. Consider a configured
   context budget and batch pruning there too, reserving output capacity.
   Extend `account_usage()` to report cached tokens so the effect is measurable.
   This run demonstrates cache reuse, not a measured speedup over mc.py.

2. **Optional native tool calling.** OpenCode sends JSON-schema tools and
   assistant tool_calls / tool results. `_chat_once()` in mc.py sends text-only
   messages; `extract_actions()` parses action fences, and `_append_obs()`
   represents observations as user messages. Native tools could reduce protocol
   repair work for compatible models. Preserve the text-action path for models
   without reliable tool support. This run had valid calls, but one run does
   not establish a general reliability advantage.

3. **Deterministic acceptance tests.** mc.py already has syntax validation and
   finish/check gates. Add explicit benchmark assertions for persisted values,
   CRUD status codes, malformed JSON, wrong field types and missing records.
   A successful shell command or a model's final claim is insufficient evidence.

Batching itself is not a missing feature: mc.py already supports `write_files`
and multiple action blocks. Likewise, this short run provides no evidence that
OpenCode's long-context compaction, delegation or failure recovery is better.
A controlled mc.py run with the same model/task/settings and repeated trials
is still needed before claiming an overall performance difference.

## Proxy fix and regression checks

The server was already threaded. Its HTTP/1.1 response framing was incomplete:
upstream chunk encoding was removed without supplying another response boundary.
The fixed proxy explicitly closes each downstream connection after the response,
and uses `read1()` to forward available bytes without waiting for a 4096-byte
buffer. It also closes upstream responses, filters hop-by-hop headers, accepts
chunked request bodies, and gives concurrent logs unique filenames.

Run `python3 -m unittest discover -s debugtools -p 'test_*.py'`.
Tests cover early SSE delivery, completed-stream EOF, concurrent requests and
redaction, chunked requests, upstream HTTP errors, and connection failures.
The early-delivery regression test times out against the original HEAD proxy.
An already-running proxy must be restarted to load these changes.

## Implementation follow-up

Both candidates are now implemented in mc.py:

- Optional `--tool-mode native`, default still `text`, with shared execution
  gates, validated arguments, streamed tool calls, correlated results, and
  complete-group history compaction/resume handling.
- Cloud history uses `--context-length` as a fallback budget, reserves output
  capacity, and preserves the request prefix until pruning is needed.
  Provider-reported cached tokens are included in usage statistics.
- `python3 -m pytest tests/ -q`: 258 tests passed.

A further GLM CRUD smoke run through the proxy successfully performed native
directory, shell, and batch file-writing calls. One completed request reported
5,376 cached tokens out of 5,749 prompt tokens. The run was deliberately
interrupted when the user requested commit/push; the full CRUD acceptance check
and the planned text-mode comparison were not completed. This is not a measured
end-to-end performance improvement. Temporary artifacts:
`/var/folders/kf/lgpt9l8s4hv4q1vv67ccq3dm0000gn/T/m2coder-mc-modes-jfekd47r/`.
