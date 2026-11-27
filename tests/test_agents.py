"""Coding agents: every log format the AI agents card reads (shaped like the real files, with the
results ccusage's parsers give for them), the readers' de-duplication, hook reports, herdr and
process detection. platform/agent_formats.py has the formats, platform/agents.py the sources."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time

UTC = dt.timezone.utc


def test_claude_usage_dedupes_streamed_records():
    from cornerwindow.platform.agents import current_window, parse_claude

    def record(mid, ts, out=10):
        return json.dumps({"type": "assistant", "timestamp": ts, "requestId": "r" + mid,
                           "message": {"id": mid, "model": "claude-opus-5-5",
                                       "usage": {"input_tokens": 5, "output_tokens": out,
                                                 "cache_read_input_tokens": 1000,
                                                 "cache_creation_input_tokens": 100}}})

    lines = [record("a", "2026-11-23T09:10:00Z"), record("a", "2026-11-23T09:10:01Z"),  # the same message twice
             record("b", "2026-11-23T10:00:00Z", 50), "not json", json.dumps({"type": "user"})]
    usages = parse_claude(lines)
    assert [u.tokens for u in usages] == [115, 155]  # input + output + cache writes; reads don't count
    now = dt.datetime(2026, 11, 23, 11, 0, tzinfo=UTC)
    # starts at the first message, not rounded to the hour: 14:10 is the latest it can reset
    assert current_window([u.at for u in usages], now) == (dt.datetime(2026, 11, 23, 9, 10, tzinfo=UTC),
                                                           dt.datetime(2026, 11, 23, 14, 10, tzinfo=UTC))
    assert current_window([u.at for u in usages], now + dt.timedelta(hours=4)) is None  # window over
    assert current_window([u.at for u in usages], dt.datetime(2026, 11, 23, 9, 5, tzinfo=UTC)) is None  # not yet
    # a message after the window ended starts the next one
    times = [dt.datetime(2026, 11, 23, 9, 10, tzinfo=UTC), dt.datetime(2026, 11, 23, 15, 30, tzinfo=UTC)]
    assert current_window(times, dt.datetime(2026, 11, 23, 16, 0, tzinfo=UTC))[0] == times[1]


def test_codex_usage_and_limits():
    from cornerwindow.platform.agents import parse_codex

    line = json.dumps({"timestamp": "2026-11-23T10:00:00Z", "type": "event_msg", "payload": {
        "type": "token_count",
        "info": {"last_token_usage": {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 300}},
        "rate_limits": {"primary": {"used_percent": 42.0, "window_minutes": 300, "resets_in_seconds": 3600},
                        "secondary": {"used_percent": 10.0, "window_minutes": 10080}}}})
    usages, limits = parse_codex([line, "{}"])
    assert usages[0].tokens == 1300 and usages[0].cache_read == 200
    assert [(l.used_percent, l.window_minutes) for l in limits] == [(42.0, 300), (10.0, 10080)]
    assert limits[0].resets_at == dt.datetime(2026, 11, 23, 11, 0, tzinfo=UTC)


def test_agent_logs_read_incrementally(tmp_path):
    from cornerwindow.platform.agents import AgentUsage

    project = tmp_path / ".claude/projects/-home-me-app"
    project.mkdir(parents=True)
    log = project / "s.jsonl"
    now = dt.datetime.now(UTC)

    def record(mid):
        return json.dumps({"type": "assistant", "timestamp": now.isoformat(), "requestId": mid,
                           "message": {"id": mid, "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1,
                                                                                           "output_tokens": 9}}})

    log.write_text(record("1") + "\n" + record("2")[:20])  # a half-written last line waits
    from cornerwindow.platform.agents import AgentReports

    usage = AgentUsage(home=tmp_path, reports=AgentReports(), herdr=None, processes=None)  # no live machine state
    (summary,) = usage.summaries(now.astimezone())
    assert summary.today_messages == 1 and summary.models == ["Sonnet 5.5"] and summary.working == ["app"]
    log.write_text(record("1") + "\n" + record("2") + "\n")
    (summary,) = usage.summaries(now.astimezone())
    assert summary.today_messages == 2 and summary.today_tokens == 20


def test_session_totals_count_what_they_grew_by(tmp_path):
    from cornerwindow.platform.agents import Droid

    folder = tmp_path / ".factory/sessions/x"
    folder.mkdir(parents=True)
    path = folder / "s.settings.json"
    path.write_text(json.dumps({"model": "glm-5", "tokenUsage": {"inputTokens": 100, "outputTokens": 10}}))
    droid = Droid(tmp_path)
    usages, _ = droid.collect(0, 0)
    assert [u.tokens for u in usages] == [110] and usages[0].model == "glm-5"
    path.write_text(json.dumps({"model": "glm-5", "tokenUsage": {"inputTokens": 150, "outputTokens": 30}}) + " ")
    usages, _ = droid.collect(0, 0)
    assert [u.tokens for u in usages] == [110, 70]  # the total grew by 70


def test_opencode_and_session_table_databases(tmp_path):
    import sqlite3
    import time

    from cornerwindow.platform.agents import Goose, OpenCode

    folder = tmp_path / ".local/share/opencode"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "opencode.db")
    db.execute("CREATE TABLE session (id TEXT, directory TEXT)")
    db.execute("CREATE TABLE message (id TEXT, session_id TEXT, time_created INT, time_updated INT, data TEXT)")
    now_ms = int(time.time() * 1000)
    db.execute("INSERT INTO session VALUES ('s', '/home/me/site')")
    for i, role in enumerate(("user", "assistant")):
        db.execute("INSERT INTO message VALUES (?, 's', ?, ?, ?)", (str(i), now_ms, now_ms, json.dumps(
            {"role": role, "modelID": "kimi", "cost": 0.02, "time": {"created": now_ms},
             "tokens": {"input": 10, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}}})))
    db.commit()
    db.close()
    usages, working = OpenCode(tmp_path).collect(time.time() - 3600, time.time())
    assert [(u.tokens, u.model, u.cost) for u in usages] == [(15, "kimi", 0.02)] and working == ["site"]

    folder = tmp_path / ".local/share/goose/sessions"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "sessions.db")
    db.execute("CREATE TABLE sessions (id TEXT, working_dir TEXT, updated_at TIMESTAMP, "
               "accumulated_input_tokens INT, accumulated_output_tokens INT)")
    stamp = dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("INSERT INTO sessions VALUES ('a', '/home/me/api', ?, 400, 40)", (stamp,))
    db.commit()
    goose = Goose(tmp_path)
    usages, working = goose.collect(time.time() - 3600, time.time())
    assert [u.tokens for u in usages] == [440] and working == ["api"]
    db.execute("UPDATE sessions SET accumulated_output_tokens = 100")
    db.commit()
    db.close()
    usages, _ = goose.collect(time.time() - 3600, time.time())
    assert [u.tokens for u in usages] == [440, 60]


def test_agent_reports_and_herdr_override_the_log_guess(tmp_path):
    from cornerwindow.platform.agents import AgentReports, AgentUsage, parse_herdr, parse_report

    assert parse_report(["waiting", "--agent", "claude"], "/home/me/app") == \
        {"agent": "claude", "state": "waiting", "project": "app"}
    assert parse_report(["done", "--agent=codex", "--project", "/x/api", '{"type":"agent-turn-complete"}'])["state"] \
        == "done"  # Codex's notify appends JSON
    for bad in ([], ["sleeping"], ["working", "--agent"]):
        try:
            parse_report(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    reports = AgentReports()
    reports.update("claude", "working", "app", at=1000)
    reports.update("aider", "waiting", "", at=1000)
    assert {r.agent for r in reports.current(now=1100)} == {"claude", "aider"}
    assert [r.agent for r in reports.current(now=1000 + 31 * 60)] == ["aider"]  # working went stale
    reports.update("aider", "idle")
    assert reports.current(now=1100) == []

    herdr = parse_herdr(json.dumps({"id": "x", "result": {"type": "agent_list", "agents": [
        {"agent": "codex", "agent_status": "blocked", "cwd": "/home/me/api"},
        {"agent": "claude", "agent_status": "working", "cwd": "/home/me/app"},
        {"agent": None, "agent_status": "idle"}]}}))
    assert [(r.agent, r.state, r.project) for r in herdr] == [("codex", "waiting", "api"), ("claude", "working", "app")]
    assert parse_herdr("not json") == []

    project = tmp_path / ".claude/projects/-home-me-app"
    project.mkdir(parents=True)
    now = dt.datetime.now(UTC)
    (project / "s.jsonl").write_text(json.dumps({"type": "assistant", "timestamp": now.isoformat(), "requestId": "r",
                                                 "message": {"id": "m", "usage": {"output_tokens": 3}}}) + "\n")
    reports = AgentReports()
    reports.update("claude", "waiting", "app")
    reports.update("my-bot", "working", "site")
    usage = AgentUsage(home=tmp_path, reports=reports, herdr=lambda: herdr, processes=None)
    by_name = {s.name: s for s in usage.summaries(now.astimezone())}
    assert by_name["Claude Code"].waiting == ["app"] and by_name["Claude Code"].working == []  # the hook wins
    assert by_name["Codex"].waiting == ["api"] and by_name["Codex"].today_messages == 0  # herdr alone
    assert by_name["My Bot"].working == ["site"]  # an agent we don't know still gets a row


def test_agents_found_by_their_process(tmp_path):
    from cornerwindow.platform.agents import AgentReports, AgentUsage, Process, cpu_seconds, program_of

    assert program_of(["/usr/bin/python3.12", "/home/me/.local/bin/aider", "--model", "x"]) == "aider"
    assert program_of(["node", "--no-warnings", "/usr/lib/node_modules/@augmentcode/auggie/dist/cli.js"]) == "cli"
    assert program_of(["/home/me/.local/bin/cursor-agent", "-p"]) == "cursor-agent"
    assert program_of(["python3"]) == ""
    assert cpu_seconds("42 (my (odd) name) S 1 2 3 4 5 6 7 8 9 10 150 50 0 0", ticks=100) == 2.0

    cpu = {"aider": 1.0}
    procs = lambda: [Process(1, ["python3", "/bin/aider"], "/home/me/site", cpu["aider"]),
                     Process(2, ["node", "/x/@augmentcode/auggie/cli.js"], "/home/me/api", 5.0),
                     Process(3, ["/home/me/.opencode/bin/opencode", "serve"], "/home/me", 9.0),  # logs cover it
                     Process(4, ["cn"], "/home/me", 1.0)]  # too short a name to trust
    usage = AgentUsage(home=tmp_path, reports=AgentReports(), herdr=None, processes=procs)
    first = {s.name: s for s in usage.summaries()}
    assert set(first) == {"Aider", "Auggie"} and first["Aider"].running == ["site"] and not first["Aider"].working
    cpu["aider"] = 2.5  # busy since the last look
    second = {s.name: s for s in usage.summaries()}
    assert second["Aider"].working == ["site"] and second["Aider"].running == []
    assert second["Auggie"].running == ["api"]

    usage.configure(hide=["auggie", "Claude Code"], herdr=False, processes=True)
    usage.processes = procs
    assert {s.name for s in usage.summaries()} == {"Aider"}
    assert "claude" not in {s.id for s in usage.shown()}
    usage.configure(processes=False)
    assert usage.summaries() == [] and not any(s.id == "aider" for s in usage.shown())


# -- each agent's format ----------------------------------------------------------------------
def lines(*records) -> list[str]:
    return [json.dumps(r) if not isinstance(r, str) else r for r in records]


def totals(rows) -> tuple:
    return (sum(r.input for r in rows), sum(r.output for r in rows), sum(r.cache_read for r in rows),
            sum(r.cache_write for r in rows), sum(r.extra for r in rows))


def test_claude_streamed_blocks_sidechains_and_subagents(tmp_path):
    from cornerwindow.platform.agents import Claude

    def record(mid, rid, out, ts="2026-11-23T10:00:00Z", model="claude-opus-5-5"):
        return {"type": "assistant", "timestamp": ts, "requestId": rid, "cwd": "/home/me/my-app",
                "message": {"id": mid, "model": model, "usage": {"input_tokens": 3, "output_tokens": out}}}

    main = tmp_path / ".claude/projects/-home-me-my-app"
    (main / "s1/subagents").mkdir(parents=True)
    (main / "s1.jsonl").write_text("\n".join(lines(
        record("m1", "r1", 5), record("m1", "r1", 40),  # one response written block by block
        record("m2", "r2", 7), record("m2", "r9", 7),  # a /btw sidechain replays it with a new request id
        record("m3", "r3", 9, model="<synthetic>"))) + "\n")  # Claude Code's own placeholder, not a call
    (main / "s1/subagents/agent-a.jsonl").write_text("\n".join(lines(record("m4", "r4", 11))) + "\n")
    usages, working = Claude(tmp_path).collect(0, time.time())
    assert sorted(u.output for u in usages) == [7, 11, 40]
    assert working == ["my-app", "my-app"]  # the dash in the name survives: it comes from `cwd`


def test_codex_records_forks_and_model():
    from cornerwindow.platform.agents import Codex, merge, parse_codex

    turn = {"type": "turn_context", "payload": {"model": "gpt-6.2"}}
    record = {"timestamp": "2026-11-23T10:00:00Z", "type": "token_usage_record", "payload": {
        "response_id": "resp_1", "usage": {"input_tokens": 25164, "cached_input_tokens": 23168, "output_tokens": 190,
                                           "cache_write_input_tokens": 0, "reasoning_output_tokens": 40}}}
    count = {"timestamp": "2026-11-23T10:00:01Z", "type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"input_tokens": 25164, "cached_input_tokens": 23168, "output_tokens": 190}}}}
    usages, _ = parse_codex(lines(turn, record, count))
    (row,) = Codex(None).items({"items": usages})  # the per-turn count repeats the record
    assert (row.input, row.cache_read, row.output, row.model) == (1996, 23168, 190, "gpt-6.2")
    fork, _ = parse_codex(lines(record))  # a fork copies the parent's records
    assert len(merge(usages + fork)) == 2 and len(merge([u for u in usages + fork if u.kind == "record"])) == 1
    old, _ = parse_codex(lines(count))  # older Codex: only counts
    assert len(Codex(None).items({"items": old})) == 1


def test_gemini_cached_input_tool_tokens_and_rewrites():
    from cornerwindow.platform.agents import merge
    from cornerwindow.platform.agent_formats import parse_gemini, parse_gemini_lines

    chat = {"sessionId": "s", "startTime": "2026-11-23T09:00:00Z", "messages": [
        {"id": "1", "type": "user", "timestamp": "2026-11-23T10:00:00Z"},
        {"id": "2", "type": "gemini", "timestamp": "2026-11-23T10:00:05Z", "model": "gemini-3-pro",
         "tokens": {"input": 1000, "output": 30, "cached": 800, "thoughts": 12, "tool": 5, "total": 1047}}]}
    (u,) = parse_gemini(chat)  # total = input + output + thoughts + tool: the cached part was inside input
    assert totals([u]) == (205, 30, 800, 0, 12) and u.model == "gemini-3-pro"
    chat["messages"][1]["tokens"]["total"] = 1847  # total counts cached apart: input was already uncached
    assert parse_gemini(chat)[0].input == 1005
    rows = parse_gemini_lines(lines(
        {"sessionId": "s", "model": "gemini-3-flash"},
        {"id": "a", "type": "gemini", "timestamp": "2026-11-23T10:00:00Z", "tokens": {"input": 10, "output": 2}},
        {"id": "a", "type": "gemini", "timestamp": "2026-11-23T10:00:00Z", "tokens": {"input": 10, "output": 9}},
        {"type": "gemini", "model": "gemini-3-flash", "tokens": {"input": 0, "output": 0}}), {})
    (u,) = merge(rows, "last")  # the reply written again as it finished
    assert (u.output, u.model) == (9, "gemini-3-flash")


def test_qwen_prompt_holds_the_cache_and_total_holds_thinking():
    from cornerwindow.platform.agent_formats import parse_qwen

    rows = parse_qwen(lines(
        {"type": "assistant", "uuid": "u1", "sessionId": "s", "timestamp": "2026-11-23T10:00:00Z", "model": "qwen3-coder",
         "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 30, "cachedContentTokenCount": 800,
                           "thoughtsTokenCount": 12, "totalTokenCount": 1042}},
        {"type": "user", "timestamp": "2026-11-23T10:00:00Z", "usageMetadata": {"promptTokenCount": 5}}))
    assert len(rows) == 1 and totals(rows) == (200, 30, 800, 0, 12)


def test_copilot_records_and_running_totals():
    from cornerwindow.platform.agents import merge
    from cornerwindow.platform.agent_formats import parse_copilot

    def snapshot(ts, inp, out, read, write):
        return {"type": "session.shutdown", "timestamp": ts, "data": {"modelMetrics": {"claude-sonnet-4.6": {
            "usage": {"inputTokens": inp, "outputTokens": out, "cacheReadTokens": read, "cacheWriteTokens": write}}}}}

    def record(ts, seq, inp, out, read):
        return {"type": "session.usage_record", "id": f"r{seq}", "timestamp": ts, "data": {"usage": {
            "model": "claude-sonnet-4.6", "inputTokens": inp, "outputTokens": out, "cacheReadTokens": read,
            "cacheWriteTokens": 0, "accounting": {"sourceSessionId": "s", "sequence": seq}}}}

    rows = merge(parse_copilot(lines(
        snapshot("2026-11-20T09:00:00Z", 500, 40, 300, 100),  # an older version: totals only
        record("2026-11-23T09:00:00Z", 1, 250, 30, 200),  # then one call recorded on its own
        {"type": "session.usage_checkpoint", "timestamp": "2026-11-23T09:00:01Z", "data": {"totalNanoAiu": 3}},
        snapshot("2026-11-23T10:00:00Z", 750, 70, 500, 100)), {}))  # totals that include that call
    assert totals(rows) == (150, 70, 500, 100, 0)  # the last totals, counted once
    assert [(r.at.day, r.input) for r in rows] == [(23, 50), (20, 100)]


def test_amp_ledger_and_messages():
    from cornerwindow.platform.agent_formats import parse_amp

    ledger = {"id": "T", "usageLedger": {"events": [
        {"id": "e1", "timestamp": "2026-11-23T10:00:00Z", "model": "claude-sonnet-4", "tokens": {"input": 1200, "output": 340},
         "toMessageId": 1},
        {"id": "e2", "timestamp": "2026-11-23T11:00:00+09:00", "model": "gpt-5", "tokens": {"total": 1500}, "toMessageId": 2}]},
        "messages": [{"role": "user"},
                     {"role": "assistant", "messageId": 1, "usage": {"cacheCreationInputTokens": 500, "cacheReadInputTokens": 7000}},
                     {"role": "assistant", "messageId": 2, "usage": {"cacheReadInputTokens": 12000}}]}
    first, second = parse_amp(ledger)  # cache counts come from the message each event points at
    assert totals([first]) == (1200, 340, 7000, 500, 0) and second.cache_read == 12000
    messages = {"id": "T2", "messages": [
        {"role": "assistant", "messageId": 10, "timestamp": "2026-11-23T10:00:00Z", "model": "gpt-5",
         "usage": {"inputTokens": 10, "outputTokens": 178, "cacheCreationInputTokens": 986, "cacheReadInputTokens": 11372}},
        {"role": "assistant", "messageId": "m11", "usage": {"model": "claude-haiku-4-5", "totalTokens": 345,
                                                              "timestamp": "2026-11-23T11:00:00Z"}}]}
    a, b = parse_amp(messages)  # model and time can sit inside usage; a bare total counts as extra
    assert a.tokens == 10 + 178 + 986 and (b.model, b.extra, b.at.hour) == ("claude-haiku-4-5", 345, 11)


def test_pi_forks_and_openclaw_models(tmp_path):
    from cornerwindow.platform.agents import OpenClaw, Pi, merge
    from cornerwindow.platform.agent_formats import parse_pi

    def message(rid, out, model="gpt-6"):
        return {"type": "message", "id": rid, "timestamp": "2026-11-23T10:00:00Z", "message": {
            "role": "assistant", "model": model, "usage": {"input": 300, "output": out, "cacheRead": 900,
                                                            "cacheWrite": 0, "totalTokens": 1200 + out, "cost": {"total": 0.25}}}}

    folder = tmp_path / ".pi/agent/sessions/--home-me-site--"
    folder.mkdir(parents=True)
    (folder / "a.jsonl").write_text("\n".join(lines(message("e1", 5), message("e2", 6))) + "\n")
    (folder / "b.jsonl").write_text("\n".join(lines(message("e1", 5), message("e3", 7))) + "\n")  # a fork
    usages, working = Pi(tmp_path).collect(0, time.time())
    assert sorted(u.output for u in usages) == [5, 6, 7] and sum(u.cost for u in usages) == 0.75
    assert working == ["site", "site"]
    rows = parse_pi(lines({"type": "model_change", "modelId": "claude-opus-5"},
                          {"type": "message", "id": "x", "timestamp": 1795000000000,
                           "message": {"role": "assistant", "usage": {"input": 1, "output": 1}}}), {})
    assert rows[0].model == "claude-opus-5"

    agent = tmp_path / ".openclaw/agents/main"
    (agent / "sessions").mkdir(parents=True)
    (agent / "agent").mkdir()
    (agent / "sessions/s.jsonl").write_text(json.dumps(message("j1", 1)) + "\n")
    db = sqlite3.connect(agent / "agent/openclaw-agent.sqlite")  # after a migration the database is the truth
    db.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, event_json TEXT, created_at INTEGER)")
    event = message("j1", 1)
    del event["timestamp"]
    db.execute("INSERT INTO transcript_events VALUES ('s', 0, ?, ?)", (json.dumps(event), int(time.time() * 1000)))
    db.commit()
    db.close()
    usages, _ = OpenClaw(tmp_path).collect(0, time.time())
    assert len(merge(usages)) == 1


def test_grok_kimi_and_deepseek():
    from cornerwindow.platform.agent_formats import parse_dsh, parse_grok, parse_kimi

    grok = parse_grok(lines(
        {"timestamp": 1795000000, "method": "_x.ai/session/update", "params": {"sessionId": "s", "update": {
            "sessionUpdate": "turn_completed", "usage": {"inputTokens": 100, "outputTokens": 20, "cachedReadTokens": 40,
                                                         "reasoningTokens": 10, "totalTokens": 120, "modelUsage": {
                "grok-4.5-build": {"inputTokens": 100, "outputTokens": 20, "cachedReadTokens": 40, "reasoningTokens": 10,
                                   "costUsdTicks": 25_000_000_000}}}}, "_meta": {"eventId": "evt-1"}}},
        {"params": {"update": {"sessionUpdate": "agent_message_chunk"}}}))
    assert totals(grok) == (60, 20, 40, 0, 0) and grok[0].cost == 2.5 and grok[0].model == "grok-4.5-build"

    kimi = parse_kimi(lines(
        {"timestamp": 1795000000, "message": {"type": "StatusUpdate", "payload": {"token_usage": {
            "input_other": 10, "output": 2, "input_cache_read": 50}}}},
        {"type": "usage.record", "model": "kimi-k2", "usageScope": "turn", "usage": {"inputOther": 7, "output": 1,
                                                                                 "inputCacheCreation": 3}, "time": 1795000000000},
        {"type": "usage.record", "usageScope": "session", "usage": {"inputOther": 17, "output": 3}, "time": 1795000000000}),
        "kimi-for-coding")
    assert totals(kimi) == (17, 3, 50, 3, 0) and [k.model for k in kimi] == ["kimi-for-coding", "kimi-k2"]

    parent = [{"type": "session", "id": "p", "isSeeded": False},
              {"type": "request/header", "data": {"header": {"config": {"model": "deepseek-v4-pro"}}}},
              {"type": "assistant/message", "seq": 3, "time": 1795000000000, "data": {
                  "usage": {"inputTokens": 1200, "outputTokens": 300, "cacheReadTokens": 8000, "totalTokens": 9500}}},
              {"type": "assistant/attempt", "seq": 4, "time": 1795000001000, "data": {"stream": [
                  {"type": "chunk", "chunk": {"type": "usage", "usage": {"inputTokens": 50, "outputTokens": 0}}}]}},
              {"type": "compaction/summary", "seq": 6, "time": 1795000002000, "data": {
                  "model": "deepseek-flash", "usage": {"inputTokens": 19000, "outputTokens": 800}}}]
    rows = parse_dsh(lines(*parent), {})
    assert totals(rows) == (20250, 1100, 8000, 0, 0) and rows[2].model == "deepseek-flash"
    fork = [dict(parent[0], id="f", isSeeded=True), parent[1], parent[2],
            {"type": "session/end-seed", "seq": 4, "data": {"inherited": True}},
            {"type": "assistant/message", "seq": 6, "time": 1795000005000, "data": {
                "usage": {"inputTokens": 3000, "outputTokens": 500, "cacheWriteTokens": 2000}}}]
    assert totals(parse_dsh(lines(*fork), {})) == (3000, 500, 0, 2000, 0)  # the copied parent part doesn't count
    seeded_only = fork[:3]
    assert parse_dsh(lines(*seeded_only), {}) == []


def test_codebuff_messages():
    from cornerwindow.platform.agent_formats import parse_codebuff

    chat = [{"variant": "user", "content": "hi"},
            {"variant": "ai", "id": "m1", "timestamp": "2026-11-23T10:00:00Z", "metadata": {
                "model": "claude-sonnet-4", "usage": {"inputTokens": 120, "outputTokens": 30, "cacheReadInputTokens": 900,
                                                      "cacheCreationInputTokens": 40, "totalTokens": 1100}}},
            {"role": "assistant", "metadata": {"codebuff": {"usage": {"prompt_tokens": 5, "completion_tokens": 1,
                                                                      "prompt_tokens_details": {"cached_tokens": 2}}}}}]
    a, b = parse_codebuff(chat, dt.datetime(2026, 11, 23, tzinfo=UTC))
    assert totals([a]) == (120, 30, 900, 40, 10) and a.model == "claude-sonnet-4"
    assert (b.input, b.cache_read, b.at.day) == (5, 2, 23)  # no time of its own: the chat's


def test_cline_extension_and_cli():
    from cornerwindow.platform.agent_formats import parse_cline

    def started(ts, info, model="claude-sonnet-4", **extra):
        return {"ts": ts, "type": "say", "say": "api_req_started", "modelInfo": {"modelId": model},
                "text": json.dumps(info), **extra}

    full = {"tokensIn": 100, "tokensOut": 20, "cacheReads": 5, "cacheWrites": 10, "cost": 0.001}
    rows = parse_cline([started(1795000000000, full),
                        started(1795000000001, full, partial=True),  # still running
                        started(1795000000002, {"tokensIn": 3, "tokensOut": 2, "cacheReads": 0}),  # incomplete
                        started(1795000000003, full, model="UNKNOWN"),
                        {"ts": 1795000000004, "type": "say", "say": "deleted_api_reqs", "text": json.dumps(full)}])
    assert len(rows) == 1 and totals(rows) == (100, 20, 5, 10, 0) and rows[0].cost == 0.001
    cli = {"sessionId": "s", "messages": [
        {"role": "assistant", "ts": 1795000000000, "id": "a1", "modelInfo": {"id": "claude-sonnet-4"},
         "metrics": {"inputTokens": 100, "outputTokens": 15, "cacheReadTokens": 25, "cacheWriteTokens": 10, "cost": 0}},
        {"role": "assistant", "ts": 1795000000000, "id": "a2", "modelInfo": {"id": "claude-sonnet-4"},
         "metrics": {"inputTokens": 3, "outputTokens": 2, "cacheReadTokens": 4, "cacheWriteTokens": 2}}]}
    assert [r.input for r in parse_cline(cli)] == [65]  # the CLI's input holds both caches; a broken row is dropped


def test_opencode_2_forks_compaction_and_migrated_rows(tmp_path):
    from cornerwindow.platform.agents import OpenCode

    folder = tmp_path / ".local/share/opencode"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "opencode.db")
    now = int(time.time() * 1000)
    db.executescript("""
        CREATE TABLE session_v2 (id TEXT, directory TEXT, fork_session_id TEXT, fork_boundary TEXT,
                                 time_busy INTEGER, time_idle INTEGER, time_updated INTEGER);
        CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER,
                                      time_updated INTEGER, data TEXT);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE session (id TEXT, directory TEXT);
    """)

    def data(out, provider="opencode", **extra):
        return json.dumps({"time": {"created": now}, "model": {"id": "deepseek-v4", "providerID": provider}, "cost": 0.01,
                           "tokens": {"input": 100, "output": out, "reasoning": 5, "cache": {"read": 50, "write": 0}},
                           **extra})

    db.execute("INSERT INTO session_v2 VALUES ('p', '/home/me/site', NULL, NULL, ?, ?, ?)", (now, now - 1, now))
    db.execute("INSERT INTO session_v2 VALUES ('f', '/home/me/site', 'p', ?, 0, 1, ?)",
               (json.dumps({"type": "through", "messageID": "p2"}), now))
    rows = [("p1", "p", "assistant", 1, data(1)), ("p2", "p", "assistant", 2, data(2)),
            ("f1", "f", "assistant", 1, data(1)), ("f2", "f", "assistant", 2, data(2)),  # the fork's copies
            ("f3", "f", "assistant", 3, data(3)),  # the fork's own call
            ("c1", "p", "compaction", 4, data(4, status="completed")),
            ("c2", "p", "compaction", 5, data(5, status="failed")),
            ("x1", "p", "assistant", 6, data(6, provider="claude-agent")),  # Claude Code counts it itself
            ("u1", "p", "user", 7, json.dumps({"tokens": {"input": 999}}))]
    for rid, sid, kind, seq, payload in rows:
        db.execute("INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)", (rid, sid, kind, seq, now, now, payload))
    legacy = json.dumps({"role": "assistant", "modelID": "kimi", "time": {"created": now},
                         "tokens": {"input": 7, "output": 7, "reasoning": 0, "cache": {"read": 0, "write": 0}}})
    db.execute("INSERT INTO message VALUES ('p1', 'p', ?, ?, ?)", (now, now, legacy))  # migrated: already counted
    db.execute("INSERT INTO message VALUES ('old', 'p', ?, ?, ?)", (now, now, legacy))
    db.commit()
    db.close()
    usages, working = OpenCode(tmp_path).collect(time.time() - 3600, time.time())
    assert sorted(u.output for u in usages) == [1, 2, 3, 4, 7]
    assert usages[0].extra == 5 and usages[0].model == "deepseek-v4" and working == ["site"]  # p is busy


def test_zcode_and_hermes_tables(tmp_path):
    from cornerwindow.platform.agents import Hermes, ZCode

    folder = tmp_path / ".zcode/cli/db"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "db.sqlite")
    db.execute("CREATE TABLE model_usage (id TEXT, session_id TEXT, started_at INTEGER, model_id TEXT, status TEXT, "
               "input_tokens INTEGER, output_tokens INTEGER, cache_creation_input_tokens INTEGER, "
               "cache_read_input_tokens INTEGER, computed_total_tokens INTEGER)")
    now = int(time.time() * 1000)
    db.execute("INSERT INTO model_usage VALUES ('a', 's', ?, 'glm-5', 'completed', 1000, 50, 100, 600, 1050)", (now,))
    db.execute("INSERT INTO model_usage VALUES ('b', 's', ?, 'glm-5', 'failed', 1000, 50, 0, 0, 1050)", (now,))
    db.commit()
    db.close()
    (row,), _ = ZCode(tmp_path).collect(time.time() - 3600, time.time())
    assert totals([row]) == (300, 50, 600, 100, 0)  # its input held both caches

    folder = tmp_path / ".hermes"
    folder.mkdir()
    db = sqlite3.connect(folder / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT, source TEXT, model TEXT, started_at REAL, ended_at REAL, "
               "input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, "
               "reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL)")
    db.execute("INSERT INTO sessions VALUES ('a', 'cli', 'gpt-5', ?, NULL, 1000, 500, 0, 0, 300, 0.25, NULL)",
               (time.time(),))  # still running: no end time yet
    db.commit()
    db.close()
    (row,), _ = Hermes(tmp_path).collect(time.time() - 3600, time.time())
    assert (row.tokens, row.cost, row.model) == (1500, 0.25, "gpt-5")  # reasoning is inside output


def _proto(*fields) -> bytes:
    """A protobuf message from (number, int | bytes) pairs."""
    def varint(n):
        out = bytearray()
        while True:
            out.append((n & 0x7F) | (0x80 if n > 0x7F else 0))
            n >>= 7
            if not n:
                return bytes(out)

    blob = b""
    for number, value in fields:
        if isinstance(value, int):
            blob += varint(number << 3) + varint(value)
        else:
            blob += varint(number << 3 | 2) + varint(len(value)) + value
    return blob


def test_antigravity_protobuf_metadata(tmp_path):
    from cornerwindow.platform.agents import Antigravity
    from cornerwindow.platform.agent_formats import parse_antigravity

    usage = _proto((2, 1200), (3, 300), (4, 40), (5, 9000), (9, 100), (10, 200), (7, b"msg-1"))
    stamp = _proto((4, _proto((1, 1795000000), (2, 500_000_000))))
    call = _proto((3, 7), (4, usage), (9, stamp), (17, _proto((2, usage))), (17, _proto((2, _proto((2, 10), (3, 5))))),
                  (19, b"gemini-3-pro"))
    rows = parse_antigravity(_proto((1, call)))
    assert len(rows) == 2  # the retry that mirrors the main attempt is dropped, the distinct one stays
    assert totals(rows[:1]) == (1200, 200, 9000, 40, 100) and rows[0].model == "gemini-3-pro"
    assert rows[0].at == dt.datetime(2026, 11, 18, 11, 6, 40, 500000, tzinfo=UTC)
    assert parse_antigravity(b"\xff\xff") == []

    folder = tmp_path / ".gemini/antigravity-cli/conversations"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "c.db")
    db.execute("CREATE TABLE gen_metadata (idx INTEGER, data BLOB)")
    fresh = _proto((4, _proto((1, int(time.time())))))
    db.execute("INSERT INTO gen_metadata VALUES (0, ?)", (_proto((1, _proto((4, usage), (9, fresh)))),))
    db.commit()
    db.close()
    usages, _ = Antigravity(tmp_path).collect(time.time() - 3600, time.time())
    assert [u.input for u in usages] == [1200]


def test_codex_resume_repeats_the_last_turn():
    from cornerwindow.platform.agents import parse_codex

    def count(ts, total):
        return {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": total}, "last_token_usage": {"input_tokens": 90140,
                                                                                "cached_input_tokens": 88320}}}}

    usages, _ = parse_codex(lines(count("2026-11-20T00:18:39Z", 1646649), count("2026-11-21T11:55:41Z", 1646649)))
    assert len(usages) == 1  # the running total didn't move: no new call (ccusage counts this one twice)


def test_codex_spawned_session_starts_with_its_parents_totals():
    from cornerwindow.platform.agents import parse_codex

    count = {"timestamp": "2026-11-21T11:59:25Z", "type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": 1649955}, "last_token_usage": {"input_tokens": 90140}}}}
    own = {"timestamp": "2026-11-21T11:59:35Z", "type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": 1742185}, "last_token_usage": {"input_tokens": 92230}}}}
    usages, _ = parse_codex(lines(
        {"type": "session_meta", "payload": {"id": "child", "parent_thread_id": "parent"}},
        {"type": "turn_context", "payload": {"model": "gpt-6.2"}},  # part of the copied history
        count, {"type": "event_msg", "payload": {"type": "task_started"}}, own))
    assert [u.input for u in usages] == [92230]


def test_herdr_asks_every_running_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from cornerwindow.platform import agents

    config = tmp_path / "herdr"
    (config / "sessions/work").mkdir(parents=True)
    (config / "herdr.sock").touch()
    (config / "sessions/work/herdr.sock").touch()
    (config / "sessions/old").mkdir()  # stopped: no socket
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert agents.herdr_sessions() == [None, "work"]
    monkeypatch.setattr(agents.shutil, "which", lambda name: "/usr/bin/herdr")
    asked = []

    def run(command, **_kw):  # herdr's real answer, as `herdr agent list` prints it
        asked.append(command)
        status = "working" if "--session" in command else "blocked"
        return SimpleNamespace(stdout=json.dumps({"id": "cli:agent:list", "result": {"type": "agent_list", "agents": [
            {"agent": "claude", "agent_status": status, "cwd": "/home/me/app", "foreground_cwd": "/home/me/app",
             "pane_id": "w1:p1"}]}}))

    states = agents.herdr_states(run)
    assert asked == [["herdr", "agent", "list"], ["herdr", "--session", "work", "agent", "list"]]
    assert [(r.state, r.project) for r in states] == [("waiting", "app"), ("working", "app")]


def test_claude_limits_from_the_status_line(tmp_path):
    from cornerwindow.platform.agents import Claude, read_limits, record_statusline, statusline_limits, statusline_text

    now = dt.datetime(2026, 11, 23, 12, 0, tzinfo=UTC).timestamp()
    payload = {"model": {"display_name": "Opus 5.5"}, "workspace": {"current_dir": "/home/me/app"},
               "rate_limits": {"five_hour": {"used_percentage": 42.4, "resets_at": int(now + 3600)},
                               "seven_day": {"used_percentage": 61, "resets_at": int(now + 86400)}}}
    assert statusline_limits(payload)["five_hour"] == {"used": 42.4, "resets_at": now + 3600}
    assert statusline_limits({}) == {} and statusline_limits({"rate_limits": None}) == {}
    assert statusline_text(payload) == "Opus 5.5 · app · 5h limit 42% · week 61%"
    path = tmp_path / ".local/share/cornerwindow/claude-limits.json"
    assert record_statusline(payload, path, now)
    assert not record_statusline(payload, path, now + 5)  # unchanged: no write
    assert not record_statusline({"model": {}}, path, now)  # no limits (an API key): keeps the old ones
    five, week = read_limits(path, now + 60)
    assert (five.used_percent, five.window_minutes) == (42.4, 300) and week.window_minutes == 7 * 24 * 60
    assert five.as_of == dt.datetime.fromtimestamp(now, UTC)
    assert [l.window_minutes for l in read_limits(path, now + 7200)] == [10080]  # the 5-hour one reset
    assert read_limits(tmp_path / "missing.json") == []
    claude = Claude(tmp_path)
    (tmp_path / ".claude").mkdir()
    claude.collect(0, now + 60)
    assert [l.used_percent for l in claude.limits] == [42.4, 61]


def test_limit_pace():
    from cornerwindow.platform.agents import Limit, pace

    now = dt.datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    soon = now + dt.timedelta(hours=1)  # 4 of 5 hours gone
    calm = pace(Limit(40, 300, soon), now)
    assert round(calm.elapsed, 2) == 0.8 and round(calm.projected) == 50 and calm.runs_out is None
    # 60% in the first hour: at that pace it's gone 40 minutes from now, long before the reset
    hot = pace(Limit(60, 300, now + dt.timedelta(hours=4)), now)
    assert round(hot.projected) == 300 and hot.runs_out == now + dt.timedelta(minutes=40)
    early = pace(Limit(5, 300, now + dt.timedelta(minutes=290)), now)
    assert early.projected is None and early.runs_out is None  # 10 minutes in: too early to say
    assert pace(Limit(5, 0, None), now) is None


def test_claude_limits_from_the_desktop_app(tmp_path):
    """The desktop app's 15-minute readings (no reset times): the window is pinned by where the %
    rose from 0, at this PC's first message there; the week by where the % fell."""
    from cornerwindow.platform.agents import best_limits, desktop_limits, read_desktop_samples

    base = dt.datetime(2026, 11, 26, 20, 0, tzinfo=UTC).timestamp()
    q = 900  # a reading every 15 minutes

    def samples(*fh, sd=(), start=base):
        return [(start + i * q, float(f), float(sd[i]) if i < len(sd) else 10.0) for i, f in enumerate(fh)]

    rows = samples(0, 0, 4, 9, 14, sd=(30, 2, 3, 4, 5))  # rose between 20:15 and 20:30; the week fell at ~20:07
    now = base + 4 * q + 60
    sent = [dt.datetime.fromtimestamp(base + q + 300, UTC)]  # this PC's message at 20:20
    five, week = desktop_limits(rows, now, sent)
    assert five.used_percent == 14 and five.resets_at == dt.datetime(2026, 11, 27, 1, 20, tzinfo=UTC)
    assert five.approx and not five.latest and five.as_of == dt.datetime.fromtimestamp(base + 4 * q, UTC)
    assert week.used_percent == 5 and week.resets_at == dt.datetime(2026, 12, 3, 20, 7, 30, tzinfo=UTC)
    # no message from this PC: between the two readings
    assert desktop_limits(rows, now)[0].resets_at == dt.datetime(2026, 11, 27, 1, 22, 30, tzinfo=UTC)
    # back at 0%: the next message starts the window
    rows = samples(9, 14, 0)
    now = base + 2 * q + 120
    (five, _week) = desktop_limits(rows, now, [dt.datetime.fromtimestamp(base + 2 * q + 60, UTC)])
    assert five.used_percent == 0 and five.resets_at == dt.datetime.fromtimestamp(base + 2 * q + 60 + 5 * 3600, UTC)
    assert desktop_limits(rows, now)[0].resets_at is None  # no message yet: the whole window is there
    # a gap in the readings (the app was closed): only the latest the week can reset
    rows = [(base, 1.0, 34.0), (base + 40 * 3600, 2.0, 0.0)]
    week = desktop_limits(rows, base + 40 * 3600 + 60)[-1]
    assert week.latest and week.resets_at == dt.datetime.fromtimestamp(base + 40 * 3600 + 7 * 86400, UTC)
    assert desktop_limits(rows, base + 50 * 3600) == []  # last reading 10 hours old: says nothing now
    # the file: the account read last, oldest first, empty readings skipped
    path = tmp_path / "plan-usage-history.json"
    path.write_text(json.dumps({"version": 2, "samples": [
        {"t": 2000_000, "org": "a", "u": {"fh": 5, "sd": 7}}, {"t": 1000_000, "org": "a", "u": {"fh": 3, "sd": 6}},
        {"t": 1500_000, "org": "b", "u": {"fh": 50}}, {"t": 2500_000, "org": "a", "u": {}}]}))
    assert read_desktop_samples(path) == [(1000.0, 3.0, 6.0), (2000.0, 5.0, 7.0)]
    assert read_desktop_samples(tmp_path / "missing.json") == []
    # the newest reading wins, but an exact reset time for the same window beats a worked-out one
    from cornerwindow.platform.agents import Limit

    old = dt.datetime(2026, 11, 27, 1, 0, tzinfo=UTC)
    exact = Limit(10, 300, dt.datetime(2026, 11, 27, 5, 0, tzinfo=UTC), old)
    guessed = Limit(12, 300, dt.datetime(2026, 11, 27, 5, 10, tzinfo=UTC), old + dt.timedelta(minutes=5), approx=True)
    (best,) = best_limits([exact], [guessed])
    assert best.used_percent == 12 and best.resets_at == exact.resets_at and not best.approx
