"""How each coding agent writes its usage, and how to read it back. Pure functions, no I/O.

Every parser returns `Usage` rows. A row's `key` names the API call it counts, so the same call
seen twice (a streamed message written in pieces, a forked session that copies its parent, a
message rewritten in place, a log migrated to a database) is counted once; `None` means "always
count". The rules follow what each tool writes, checked against ccusage's parsers and fixtures
(github.com/ccusage/ccusage, rust/adapters/*), which track these formats in production.

Token fields everywhere: `input` excludes cached input, `cache_read`/`cache_write` are the cache
buckets, `output` is output (including reasoning where the tool folds it in), and `extra` holds
tokens a tool counts beyond those: reasoning it reports separately, and whatever its own total
holds that the parts don't (ccusage's "report remainder").
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass


@dataclass
class Usage:
    at: dt.datetime
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    model: str = ""
    extra: int = 0  # reasoning counted apart from output, and the rest of the tool's own total
    cost: float = 0.0  # USD, when the tool records it
    key: object = None  # the call it counts; the same key twice is one call
    kind: str = ""  # for sources that write the same call twice in different shapes

    @property
    def tokens(self) -> int:
        """What counts against a plan: everything except cheap cache reads."""
        return self.input + self.output + self.extra + self.cache_write

    @property
    def empty(self) -> bool:
        return not (self.input or self.output or self.cache_read or self.cache_write or self.extra)


@dataclass
class Limit:
    used_percent: float
    window_minutes: int
    resets_at: dt.datetime | None


# -- small helpers -------------------------------------------------------------------------
def to_time(value) -> dt.datetime | None:
    """ISO text, or epoch seconds / milliseconds, as an aware datetime."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        seconds = value / 1000 if value > 1e11 else value
        try:
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return to_time(float(text))
    try:
        at = dt.datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)  # SQLite timestamps are UTC


def num(value) -> int:
    """A token count: a non-negative integer, or 0 for anything else (text, bools, nonsense)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value != value or value < 0 or value > 2**53:  # NaN, negative, absurd
        return 0
    return int(value)


def money(value) -> float:
    if isinstance(value, dict):
        value = value.get("total")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 0.0
    return float(value) if 0 < value < 1e6 else 0.0


def pick(d, *keys) -> int:
    """The first of `keys` that `d` has as a number."""
    if not isinstance(d, dict):
        return 0
    for key in keys:
        if key in d and isinstance(d[key], (int, float)) and not isinstance(d[key], bool):
            return num(d[key])
    return 0


def text(d, *keys) -> str:
    if isinstance(d, dict):
        for key in keys:
            value = d.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def remainder(total: int, *parts: int) -> int:
    """What a tool's own total holds beyond the parts it itemises."""
    return max(0, total - sum(parts))


def json_lines(lines):
    for line in lines:
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            yield value


def _dig(d, *path):
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


# -- Claude Code ---------------------------------------------------------------------------
def parse_claude(lines, seen: set | None = None) -> list[Usage]:
    """Assistant messages with usage. Claude Code writes each content block of a response on its
    own line with the same message id (and copies responses into sidechains and other
    transcripts), so a message id is one call; the copy with the most output wins."""
    best: dict = {}
    loose = []
    for record in json_lines(lines):
        if record.get("type") != "assistant":
            continue
        message = record.get("message") or {}
        usage = message.get("usage")
        at = to_time(record.get("timestamp"))
        if not isinstance(usage, dict) or at is None:
            continue
        row = Usage(at, pick(usage, "input_tokens"), pick(usage, "output_tokens"),
                    pick(usage, "cache_read_input_tokens"), pick(usage, "cache_creation_input_tokens"),
                    text(message, "model"))
        if row.model == "<synthetic>" or row.empty:
            continue
        mid, rid = message.get("id"), record.get("requestId")
        row.key = ("claude", mid) if mid else ("claude-r", rid, record.get("timestamp")) if rid else None
        if row.key is None:
            loose.append(row)
        elif row.key not in best or row.tokens > best[row.key].tokens:
            best[row.key] = row
    out = list(best.values()) + loose
    if seen is not None:  # callers reading in chunks: drop what an earlier chunk already had
        out = [u for u in out if u.key is None or u.key not in seen or seen.add(u.key)]
        seen.update(u.key for u in out if u.key is not None)
    return out


# -- Codex ---------------------------------------------------------------------------------
def parse_codex(lines, state: dict | None = None) -> tuple[list[Usage], list[Limit]]:
    """Per-response `token_usage_record` lines (newer Codex, keyed by response id, so a fork's
    copies count once) and per-turn `token_count` events (all versions; kind "count"). A file with
    records should be read from those alone. A count whose running total didn't move repeats the
    last turn (Codex writes one when a session is resumed) and isn't a call, and neither are the
    counts a spawned or forked session copies from its parent before its first turn. Codex's input includes
    cached input and its output includes reasoning. `turn_context` lines say the model."""
    state = {} if state is None else state
    usages, limits = [], []
    for record in json_lines(lines):
        kind = record.get("type")
        payload = record.get("payload") or {}
        if kind == "session_meta":  # a spawned or forked session starts with its parent's totals
            state["inherited"] = bool(payload.get("parent_thread_id") or payload.get("forked_from_id"))
            continue
        if payload.get("type") == "task_started":  # its own first turn (the copied history has turn contexts)
            state["inherited"] = False
        if kind == "turn_context":
            state["model"] = text(payload, "model") or state.get("model", "")
            continue
        at = to_time(record.get("timestamp"))
        if kind == "token_usage_record":
            usage, rid = payload.get("usage"), text(payload, "response_id")
            if isinstance(usage, dict) and rid and at:
                row = _codex_usage(at, usage, state.get("model", ""))
                if not row.empty:
                    row.key, row.kind = ("codex", rid), "record"
                    usages.append(row)
            continue
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        last, total = info.get("last_token_usage"), info.get("total_token_usage")
        repeated = isinstance(total, dict) and total == state.get("total")  # a resume repeats the last turn
        if isinstance(total, dict):
            state["total"] = total
        if at is not None and isinstance(last, dict) and not repeated and not state.get("inherited"):
            row = _codex_usage(at, last, state.get("model", ""))
            if not row.empty:
                row.kind = "count"
                usages.append(row)
        found = []
        for name in ("primary", "secondary"):
            rate = (payload.get("rate_limits") or {}).get(name) or {}
            if "used_percent" not in rate:
                continue
            resets = None
            if rate.get("resets_at"):
                resets = to_time(rate["resets_at"])
            elif rate.get("resets_in_seconds") is not None and at is not None:
                try:
                    resets = at + dt.timedelta(seconds=float(rate["resets_in_seconds"]))
                except (TypeError, ValueError):
                    resets = None
            try:
                found.append(Limit(float(rate["used_percent"]), int(rate.get("window_minutes") or 0), resets))
            except (TypeError, ValueError):
                continue
        if found:
            limits = found
    return usages, limits


def _codex_usage(at, usage: dict, model: str) -> Usage:
    cached = min(pick(usage, "cached_input_tokens"), pick(usage, "input_tokens"))
    return Usage(at, pick(usage, "input_tokens") - cached, pick(usage, "output_tokens"), cached, 0, model)


# -- Gemini CLI ----------------------------------------------------------------------------
_GEMINI = {
    "input": ("input", "prompt", "input_tokens", "prompt_tokens"),
    "output": ("output", "candidates", "output_tokens", "candidates_tokens"),
    "cached": ("cached", "cached_tokens"),
    "thoughts": ("thoughts", "reasoning", "thoughts_tokens", "reasoning_tokens"),
    "tool": ("tool", "tool_tokens"),
}


def _gemini_tokens(tokens) -> dict | None:
    if not isinstance(tokens, dict):
        return None
    out = {name: pick(tokens, *keys) for name, keys in _GEMINI.items()}
    total = tokens.get("total", tokens.get("total_tokens"))
    out["total"] = num(total) if total is not None else None
    return out


def _gemini_usage(at, t: dict, model: str, session: bool) -> Usage | None:
    """Gemini's `input` (promptTokenCount) normally includes the cached part. In chat files it is
    taken out only when the total shows it was in (total = input + output + thoughts + tool)."""
    inclusive = t["input"] + t["output"] + t["thoughts"] + t["tool"]
    if not session or (t["cached"] and t["total"] == inclusive and t["total"] != inclusive + t["cached"]):
        cached_part = min(t["input"], t["cached"])
        uncached = t["input"] - cached_part
    else:
        uncached = t["input"]
    inp = uncached + t["tool"]
    total = t["total"] if t["total"] is not None else inp + t["output"] + t["cached"] + t["thoughts"]
    row = Usage(at, inp, t["output"], t["cached"], 0, model,
                t["thoughts"] + remainder(total, inp, t["output"], t["cached"], t["thoughts"]))
    return None if row.empty else row


def parse_gemini_record(record: dict, model: str = "", fallback=None, session_id: str = "") -> list[Usage]:
    """One Gemini record: a reply ({"type": "gemini", "tokens": {...}}), or a run's `stats`."""
    at = to_time(record.get("timestamp") or record.get("createdAt")) or fallback
    if record.get("type") == "gemini":
        t = _gemini_tokens(record.get("tokens"))
        row = _gemini_usage(at, t, text(record, "model") or model, True) if t and at else None
        if row is None:
            return []
        if record.get("id"):
            row.key = ("gemini", session_id, record["id"])
        return [row]
    stats = record.get("stats") or _dig(record, "result", "stats")
    if not isinstance(stats, dict) or at is None:
        return []
    out = []
    models = stats.get("models")
    if isinstance(models, dict):
        for name, entry in models.items():
            t = _gemini_tokens((entry or {}).get("tokens") if isinstance(entry, dict) else None)
            row = _gemini_usage(at, t, name, False) if t else None
            out += [row] if row else []
        return out
    t = _gemini_tokens(stats)
    row = _gemini_usage(at, t, model, False) if t else None
    return [row] if row else []


def parse_gemini(data, fallback=None) -> list[Usage]:
    """A Gemini CLI chat file (JSON): {"sessionId", "startTime", "messages": [...]}."""
    if isinstance(data, list):
        data = {"messages": data}
    if not isinstance(data, dict):
        return []
    session = text(data, "sessionId", "session_id")
    start = to_time(data.get("startTime") or data.get("lastUpdated")) or fallback
    messages = data.get("messages")
    if isinstance(messages, list):
        return [u for m in messages if isinstance(m, dict) and m.get("type") == "gemini"
                for u in parse_gemini_record(m, "", start, session)]
    return parse_gemini_record(data, text(data, "model"), start, session)


def parse_gemini_lines(lines, state: dict) -> list[Usage]:
    """Newer Gemini CLI chats (JSONL). A reply can be written again as it completes: the same id
    later in the file replaces it (the source keeps the last copy)."""
    out = []
    for record in json_lines(lines):
        state["session"] = text(record, "sessionId", "session_id") or state.get("session", "")
        state["model"] = text(record, "model") or state.get("model", "")
        out += parse_gemini_record(record, state.get("model", ""), None, state.get("session", ""))
    return out


# -- Qwen Code -----------------------------------------------------------------------------
def parse_qwen(lines) -> list[Usage]:
    """Assistant lines with `usageMetadata` (Gemini-style counts). The prompt includes cached
    input; thinking is whatever the total holds beyond prompt and candidates."""
    out = []
    for record in json_lines(lines):
        meta = record.get("usageMetadata")
        at = to_time(record.get("timestamp"))
        if record.get("type") != "assistant" or not isinstance(meta, dict) or at is None:
            continue
        prompt, total = pick(meta, "promptTokenCount"), pick(meta, "totalTokenCount")
        cached = min(pick(meta, "cachedContentTokenCount"), prompt)
        candidates = meta.get("candidatesTokenCount")
        if isinstance(candidates, (int, float)) and not isinstance(candidates, bool):
            output = min(num(candidates), total - prompt) if total > prompt else num(candidates)
        else:
            output = max(0, total - prompt) if "promptTokenCount" in meta else 0
        extra = remainder(total, prompt - cached, output, cached)
        if candidates is None and total == 0:
            extra = pick(meta, "thoughtsTokenCount")
        row = Usage(at, prompt - cached, output, cached, 0, text(record, "model"), extra)
        if not row.empty:
            uid = text(record, "uuid")
            row.key = ("qwen", text(record, "sessionId"), uid) if uid else None
            out.append(row)
    return out


# -- GitHub Copilot CLI --------------------------------------------------------------------
def _copilot_tokens(usage: dict) -> tuple[int, int, int, int]:
    """(uncached input, output, cache read, cache write). Copilot's input includes both caches;
    its reasoning is part of output."""
    read, write = pick(usage, "cacheReadTokens"), pick(usage, "cacheWriteTokens")
    return max(0, pick(usage, "inputTokens") - read - write), pick(usage, "outputTokens"), read, write


def parse_copilot(lines, state: dict) -> list[Usage]:
    """Copilot's session events. `session.usage_record` is one call. `session.shutdown` (and a
    checkpoint with model metrics) holds the session's running totals per model, which also cover
    the calls recorded one by one: only what they add beyond the records counts, at their time."""
    records = state.setdefault("records", {})  # model -> (in, out, read, write) summed from records
    unexplained = state.setdefault("unexplained", {})  # model -> what snapshots held beyond records
    out = []
    for index, record in enumerate(json_lines(lines)):
        kind = record.get("type")
        at = to_time(record.get("timestamp"))
        data = record.get("data") or {}
        if at is None or not isinstance(data, dict):
            continue
        if kind == "session.usage_record":
            usage = data.get("usage")
            if not isinstance(usage, dict):
                continue
            model = text(usage, "model")
            tokens = _copilot_tokens(usage)
            accounting = usage.get("accounting") or {}
            key = ("copilot", text(accounting, "sourceSessionId"), accounting.get("sequence")) \
                if isinstance(accounting, dict) and accounting.get("sequence") is not None else \
                ("copilot", text(record, "id")) if text(record, "id") else None
            row = Usage(at, *tokens, model=model, key=key)
            if not row.empty:
                out.append(row)
                records[model] = tuple(a + b for a, b in zip(records.get(model, (0, 0, 0, 0)), tokens))
        elif kind in ("session.shutdown", "session.usage_checkpoint"):
            metrics = data.get("modelMetrics")
            if not isinstance(metrics, dict):
                continue
            for model, entry in metrics.items():
                usage = (entry or {}).get("usage") if isinstance(entry, dict) else None
                if not isinstance(usage, dict):
                    continue
                total = _copilot_tokens(usage)
                beyond = tuple(max(0, t - r) for t, r in zip(total, records.get(model, (0, 0, 0, 0))))
                before = unexplained.get(model, (0, 0, 0, 0))
                grew = tuple(max(0, b - a) for a, b in zip(before, beyond))
                unexplained[model] = tuple(max(a, b) for a, b in zip(before, beyond))
                row = Usage(at, *grew, model=model, key=None)
                if not row.empty:
                    out.append(row)
    return out


# -- Amp -----------------------------------------------------------------------------------
def parse_amp(thread) -> list[Usage]:
    """An Amp thread. Its usage ledger, when there is one, is the record of calls; the cache
    counts sit on the message each ledger event points at (`toMessageId`). Older threads only
    have usage on their messages (model and time may sit inside `usage`)."""
    if not isinstance(thread, dict):
        return []
    tid = text(thread, "id")
    messages = [m for m in thread.get("messages") or [] if isinstance(m, dict)]
    ledger = _dig(thread, "usageLedger", "events")
    out = []
    if isinstance(ledger, list):
        caches = {}
        for m in messages:
            usage = m.get("usage")
            if m.get("role") == "assistant" and isinstance(usage, dict) and isinstance(m.get("messageId"), int):
                caches[m["messageId"]] = (pick(usage, "cacheCreationInputTokens"), pick(usage, "cacheReadInputTokens"))
        for index, event in enumerate(ledger):
            tokens = event.get("tokens") if isinstance(event, dict) else None
            at = to_time(event.get("timestamp")) if isinstance(event, dict) else None
            if not isinstance(tokens, dict) or at is None:
                continue
            write, read = caches.get(event.get("toMessageId"), (0, 0))
            inp, outp = pick(tokens, "input"), pick(tokens, "output")
            row = Usage(at, inp, outp, read, write, text(event, "model"),
                        remainder(pick(tokens, "total"), inp, outp, read, write),
                        key=("amp", tid, text(event, "id") or index))
            if not row.empty:
                out.append(row)
        return out
    for index, m in enumerate(messages):
        usage = m.get("usage")
        if m.get("role") != "assistant" or not isinstance(usage, dict):
            continue
        at = to_time(m.get("timestamp") or usage.get("timestamp"))
        if at is None:
            continue
        inp, outp = pick(usage, "inputTokens"), pick(usage, "outputTokens")
        write, read = pick(usage, "cacheCreationInputTokens"), pick(usage, "cacheReadInputTokens")
        row = Usage(at, inp, outp, read, write, text(m, "model") or text(usage, "model"),
                    remainder(pick(usage, "totalTokens"), inp, outp, read, write),
                    key=("amp", tid, m.get("messageId", index)))
        if not row.empty:
            out.append(row)
    return out


# -- Factory Droid -------------------------------------------------------------------------
def droid_totals(settings) -> Usage | None:
    """A Droid session's running totals (*.settings.json). The time is set by the reader."""
    usage = settings.get("tokenUsage") if isinstance(settings, dict) else None
    if not isinstance(usage, dict):
        return None
    inp, outp = pick(usage, "inputTokens"), pick(usage, "outputTokens")
    write, read = pick(usage, "cacheCreationTokens"), pick(usage, "cacheReadTokens")
    thinking = pick(usage, "thinkingTokens")
    return Usage(None, inp, outp, read, write, text(settings, "model"),
                 thinking + remainder(pick(usage, "totalTokens"), inp, outp, read, write, thinking))


# -- pi, Oh My Pi, OpenClaw (pi's session format) ------------------------------------------
def parse_pi(lines, state: dict) -> list[Usage]:
    """{"type": "message", "id", "timestamp", "message": {"role": "assistant", "model", "usage":
    {"input", "output", "cacheRead", "cacheWrite", "totalTokens", "cost": {"total"}}}}. Entry ids
    are kept when a session is forked, so a copy in the fork counts once. OpenClaw names the model
    `modelId` and can announce it in an earlier `model_change` line."""
    out = []
    for record in json_lines(lines):
        kind = record.get("type")
        if kind == "model_change" or (kind == "custom" and record.get("customType") == "model-snapshot"):
            source = record.get("data") if isinstance(record.get("data"), dict) else record
            state["model"] = text(source, "modelId", "model") or state.get("model", "")
            continue
        message = record.get("message")
        if kind != "message" or not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        at = to_time(record.get("timestamp")) or to_time(message.get("timestamp"))
        if not isinstance(usage, dict) or at is None:
            continue
        inp, outp = pick(usage, "input"), pick(usage, "output")
        read, write = pick(usage, "cacheRead"), pick(usage, "cacheWrite")
        model = text(message, "modelId", "model") or state.get("model", "")
        row = Usage(at, inp, outp, read, write, model, remainder(pick(usage, "totalTokens"), inp, outp, read, write),
                    money(usage.get("cost")))
        if not row.empty:
            rid = text(record, "id")
            row.key = ("pi", rid, record.get("timestamp")) if rid else None
            out.append(row)
    return out


# -- Grok Build ----------------------------------------------------------------------------
def parse_grok(lines) -> list[Usage]:
    """`turn_completed` session updates: a turn's usage, per model when `modelUsage` has them.
    Grok's inputTokens includes both cache buckets, reasoning is part of output, and
    costUsdTicks is in units of 1e-10 USD."""
    out = []
    for line in lines:
        if "turn_completed" not in line:
            continue
        for record in json_lines([line]):
            params = record.get("params") or {}
            update = params.get("update") or {}
            if update.get("sessionUpdate") != "turn_completed" or not isinstance(update.get("usage"), dict):
                continue
            meta = params.get("_meta") or record.get("_meta") or {}
            at = to_time(meta.get("agentTimestampMs")) if isinstance(meta, dict) else None
            at = at or to_time(record.get("timestamp"))
            if at is None:
                continue
            usage = update["usage"]
            event = text(meta, "eventId") if isinstance(meta, dict) else ""
            per_model = usage.get("modelUsage")
            rows = per_model.items() if isinstance(per_model, dict) and per_model else [("", usage)]
            for model, numbers in rows:
                if not isinstance(numbers, dict):
                    continue
                inp = pick(numbers, "inputTokens")
                read = min(pick(numbers, "cachedReadTokens"), inp)
                write = min(pick(numbers, "cacheCreationTokens"), inp - read)
                row = Usage(at, inp - read - write, pick(numbers, "outputTokens"), read, write, model,
                            cost=pick(numbers, "costUsdTicks") / 1e10,
                            key=("grok", text(params, "sessionId"), event, model) if event else None)
                if not row.empty:
                    out.append(row)
    return out


# -- Kimi Code -----------------------------------------------------------------------------
def parse_kimi(lines, model: str = "") -> list[Usage]:
    """Kimi's wire.jsonl: modern `usage.record` lines (only those scoped to a turn; others repeat
    them) and legacy `StatusUpdate` messages with `token_usage`."""
    out = []
    for record in json_lines(lines):
        if record.get("type") == "usage.record":
            if record.get("usageScope") != "turn":
                continue
            usage, at = record.get("usage"), to_time(record.get("time"))
            names = ("inputOther", "output", "inputCacheRead", "inputCacheCreation", "total")
            key = text(record, "messageId", "id")
            row_model = text(record, "model") or model
        else:
            message = record.get("message") or {}
            if not isinstance(message, dict) or message.get("type") != "StatusUpdate":
                continue
            payload = message.get("payload") or {}
            usage, at = payload.get("token_usage"), to_time(record.get("timestamp"))
            names = ("input_other", "output", "input_cache_read", "input_cache_creation", "total")
            key = text(payload, "message_id")
            row_model = model
        if not isinstance(usage, dict) or at is None:
            continue
        inp, outp, read, write = (pick(usage, n) for n in names[:4])
        total = pick(usage, names[4], "total")
        row = Usage(at, inp, outp, read, write, row_model, remainder(total, inp, outp, read, write),
                    key=("kimi", key) if key else None)
        if not row.empty:
            out.append(row)
    return out


# -- DeepSeek Harness ----------------------------------------------------------------------
def parse_dsh(lines, state: dict) -> list[Usage]:
    """DeepSeek Harness sessions. `assistant/message` and `assistant/attempt` events carry the
    call's usage (or, without settled usage, the last streamed usage chunk). The model comes from
    the latest request header. `compaction/summary` events are calls too, on their own model. A
    forked session first copies its parent up to a line with
    `"inherited": true`; only usage after the last such marker belongs to it. Its input already
    excludes the cache buckets."""
    out = []
    for record in json_lines(lines):
        kind = record.get("type")
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        if kind == "session":
            state["seeded"] = bool(record.get("isSeeded"))
            state["session"] = text(record, "id")
            state["cwd"] = text(record, "cwd")
            state["pending"] = []
            continue
        if data.get("inherited"):
            state["pending"] = []  # all before it was the parent's
            state["seeded"] = False
            continue
        if kind in ("request/header", "request/context"):
            route = _dig(data, "header", "config") if kind == "request/header" else data
            state["model"] = text(route, "model") or state.get("model", "")
            continue
        if kind not in ("assistant/message", "assistant/attempt", "compaction/summary"):
            continue
        usage = data.get("usage")
        if not isinstance(usage, dict) and kind != "compaction/summary":
            usage = None
            for item in reversed(data.get("stream") or []):
                chunk = item.get("chunk") if isinstance(item, dict) and item.get("type") == "chunk" else None
                if isinstance(chunk, dict) and chunk.get("type") == "usage" and isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                    break
        at = to_time(record.get("time"))
        if usage is None or at is None:
            continue
        inp, outp = pick(usage, "inputTokens"), pick(usage, "outputTokens")
        read, write = pick(usage, "cacheReadTokens"), pick(usage, "cacheWriteTokens")
        model = text(data, "model") if kind == "compaction/summary" else ""  # compaction runs on its own model
        row = Usage(at, inp, outp, read, write, model or state.get("model", ""),
                    remainder(pick(usage, "totalTokens"), inp, outp, read, write),
                    key=("dsh", state.get("session", ""), record.get("seq")))
        if row.empty:
            continue
        (state.setdefault("pending", []) if state.get("seeded") else out).append(row)
    return out


# -- Codebuff ------------------------------------------------------------------------------
def parse_codebuff(messages, chat_time=None) -> list[Usage]:
    """Codebuff's chat-messages.json: a list of messages; assistant ones (`variant` or `role` ai,
    agent or assistant) carry usage in metadata.usage or metadata.codebuff.usage."""
    out = []
    for index, m in enumerate(messages if isinstance(messages, list) else []):
        if not isinstance(m, dict) or (text(m, "variant") or text(m, "role")) not in ("ai", "agent", "assistant"):
            continue
        meta = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else _dig(meta, "codebuff", "usage")
        if not isinstance(usage, dict):
            continue
        at = to_time(m.get("timestamp") or m.get("createdAt") or meta.get("timestamp")) or chat_time
        if at is None:
            continue
        inp = pick(usage, "inputTokens", "input_tokens", "promptTokens", "prompt_tokens")
        outp = pick(usage, "outputTokens", "output_tokens", "completionTokens", "completion_tokens")
        read = pick(usage, "cacheReadInputTokens", "cache_read_input_tokens") or \
            pick(usage.get("promptTokensDetails") or {}, "cachedTokens") or \
            pick(usage.get("prompt_tokens_details") or {}, "cached_tokens")
        write = pick(usage, "cacheCreationInputTokens", "cache_creation_input_tokens", "cacheCreationTokens",
                     "cache_creation_tokens", "cachedTokensCreated", "cached_tokens_created")
        row = Usage(at, inp, outp, read, write, text(meta, "model") or text(usage, "model"),
                    remainder(pick(usage, "totalTokens", "total_tokens", "total"), inp, outp, read, write),
                    key=("codebuff", text(m, "id") or index))
        if not row.empty:
            out.append(row)
    return out


# -- Cline ---------------------------------------------------------------------------------
def _ints(d: dict, *keys) -> tuple | None:
    """All of `keys` as non-negative integers, or None when one is missing or not a count."""
    values = tuple(d.get(k) for k in keys)
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        return None
    return values


def _cline_model(info, key: str) -> str:
    model = text(info, key) if isinstance(info, dict) else ""
    return "" if model.lower() == "unknown" else model


def parse_cline(data) -> list[Usage]:
    """The VS Code extension's ui_messages.json (finished `api_req_started` messages, numbers as
    JSON text; tokensIn excludes the caches) or the CLI's *.messages.json (assistant messages with
    `metrics`, whose inputTokens includes both caches). Like ccusage, a request counts only with
    its model and all four counts present (anything else is a request still running, a deleted
    one, or a record too broken to trust)."""
    out = []
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        for m in data["messages"]:
            metrics = m.get("metrics") if isinstance(m, dict) and m.get("role") == "assistant" else None
            if not isinstance(metrics, dict):
                continue
            counts = _ints(metrics, "inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
            model = _cline_model(m.get("modelInfo"), "id")
            at = to_time(m.get("ts")) or to_time(m.get("timestamp"))
            if counts is None or not model or at is None or counts[0] < counts[2] + counts[3]:
                continue
            inp, outp, read, write = counts
            row = Usage(at, inp - read - write, outp, read, write, model, cost=money(metrics.get("cost")),
                        key=("cline", text(data, "sessionId"), text(m, "id")) if text(m, "id") else None)
            if not row.empty:
                out.append(row)
        return out
    for m in data if isinstance(data, list) else []:
        if not isinstance(m, dict) or m.get("type") != "say" or m.get("say") != "api_req_started" or m.get("partial"):
            continue
        try:
            info = json.loads(m.get("text") or "")
        except (TypeError, ValueError):
            continue
        counts = _ints(info, "tokensIn", "tokensOut", "cacheReads", "cacheWrites") if isinstance(info, dict) else None
        model = _cline_model(m.get("modelInfo"), "modelId")
        at = to_time(m.get("ts"))
        if counts is None or not model or at is None:
            continue
        inp, outp, read, write = counts
        row = Usage(at, inp, outp, read, write, model, cost=money(info.get("cost")))
        if not row.empty:
            out.append(row)
    return out


# -- OpenCode and Kilo ---------------------------------------------------------------------
def opencode_message(data, fallback_ms=None) -> Usage | None:
    """An OpenCode/Kilo assistant message's JSON: tokens {input, output, reasoning, cache: {read,
    write}, total}, cost, and the model as `modelID` (1.x) or `model.id` (2.x). Messages from the
    claude-agent provider are Claude Code's own and already counted from its transcripts."""
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None
    model = data.get("model")
    provider = text(model, "providerID") if isinstance(model, dict) else text(data, "providerID")
    if provider == "claude-agent":
        return None
    at = to_time(_dig(data, "time", "created")) or to_time(fallback_ms)
    if at is None:
        return None
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    inp, outp, reasoning = pick(tokens, "input"), pick(tokens, "output"), pick(tokens, "reasoning")
    read, write = pick(cache, "read"), pick(cache, "write")
    name = text(model, "id") if isinstance(model, dict) else text(data, "modelID")
    row = Usage(at, inp, outp, read, write, name,
                reasoning + remainder(pick(tokens, "total"), inp, outp, read, write, reasoning), money(data.get("cost")))
    return None if row.empty else row


# -- Antigravity (protobuf) ----------------------------------------------------------------
def _varint(blob: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if i >= len(blob) or shift > 63:
            raise ValueError("truncated varint")
        byte = blob[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def proto_fields(blob: bytes) -> list[tuple[int, object]]:
    """(field number, int or bytes) for each field of a protobuf message; ValueError when broken."""
    out, i = [], 0
    while i < len(blob):
        tag, i = _varint(blob, i)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("field 0")
        if wire == 0:
            value, i = _varint(blob, i)
        elif wire == 1:
            value, i = None, i + 8
        elif wire == 2:
            length, i = _varint(blob, i)
            value, i = blob[i:i + length], i + length
            if len(value) != length:
                raise ValueError("truncated")
        elif wire == 5:
            value, i = None, i + 4
        else:
            raise ValueError("wire type")
        if i > len(blob):
            raise ValueError("truncated")
        out.append((number, value))
    return out


def _field(fields, number, kind=bytes):
    return next((v for n, v in fields if n == number and isinstance(v, kind)), None)


def _antigravity_usage(blob: bytes) -> tuple | None:
    f = proto_fields(blob)
    get = lambda n: _field(f, n, int) or 0  # noqa: E731
    usage = (get(2), get(3), get(4), get(5), get(9), get(10))  # input, total out, write, read, reasoning, visible
    mid = _field(f, 7) or _field(f, 11) or _field(f, 12)
    return usage if any(usage) else None, mid.decode("utf-8", "replace") if mid else None


def parse_antigravity(blob: bytes) -> list[Usage]:
    """One gen_metadata row: field 1 (the chat model call) holds the usage (4), retries (17, each
    with its usage in 2; one of them repeats the main attempt), the model's name (19 or 21) and the
    time (9 -> 4 -> {seconds, nanos})."""
    try:
        call = _field(proto_fields(blob), 1)
        if call is None:
            return []
        fields = proto_fields(call)
        main = _field(fields, 4)
        attempts = [_antigravity_usage(main)] if main else []
        retries = []
        for n, value in fields:
            if n == 17 and isinstance(value, bytes):
                inner = _field(proto_fields(value), 2)
                if inner:
                    retries.append(_antigravity_usage(inner))
        if attempts and attempts[0][0] in [r[0] for r in retries]:
            retries.pop([r[0] for r in retries].index(attempts[0][0]))
        at = None
        info = _field(fields, 9)
        stamp = _field(proto_fields(info), 4) if info else None
        if stamp:
            sf = proto_fields(stamp)
            seconds = _field(sf, 1, int)
            if seconds:
                at = to_time(seconds + (_field(sf, 2, int) or 0) / 1e9)
        name = _field(fields, 19) or _field(fields, 21)
        model = name.decode("utf-8", "replace") if name else ""
    except ValueError:
        return []
    out = []
    for usage, mid in attempts + retries:
        if usage is None or at is None:
            continue
        inp, total_out, write, read, reasoning, visible = usage
        total_out = max(total_out, visible + reasoning)
        output = max(visible, total_out - reasoning)
        extra = max(reasoning, total_out - output)
        out.append(Usage(at, inp, output, read, write, model, extra, key=("antigravity", mid) if mid else None))
    return out
