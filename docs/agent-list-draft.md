# AI agents card: agent list draft

Draft for expanding `platform/agents.py` beyond Claude Code and Codex.

**Done (all five steps), and tested.** Every parser follows ccusage's adapter for that agent
(`platform/agent_formats.py`), with a test per format in `tests/test_agents.py`. Compared with
ccusage on this machine's real logs, day by day, token for token: Claude Code, Codex, OpenCode,
Oh My Pi and DeepSeek Harness (zstd-compressed) all match. The one difference: Codex writes a
repeat of the last turn when a session is resumed, which ccusage counts and the card doesn't
(Codex's own running total shows no new tokens). Cline, DeepSeek Harness and Copilot also match
ccusage's own fixtures. herdr was checked live, hooks end to end over D-Bus.
**Not verifiable here** (no such agent installed): the other 15 log formats are checked only
against ccusage's code, fixtures and tests. **Left:** Crush and Mistral Vibe usage (found by
process only), Copilot's optional OpenTelemetry export files.

Rule we keep: read local files only. No sign-in, no network, nothing sent anywhere. An agent
shows up only when its folder exists and has activity today, so a long list costs nothing.

Status column: **local** = I checked the format on this machine; **ccusage** = taken from the
ccusage per-source docs (they parse these files in production); **unverified** = from memory or
partial docs, check before writing a parser.

## 1. Harnesses: tokens (and cost) from local logs

| Agent | Where | Format | Tokens | Cost | Limits | Status |
|---|---|---|---|---|---|---|
| Claude Code | `$CLAUDE_CONFIG_DIR` or `~/.claude/projects/*/*.jsonl` (+ `~/.config/claude/projects`) | JSONL | yes | no (estimate) | 5-hour window, inferred | done |
| Codex | `$CODEX_HOME` or `~/.codex/sessions/**/*.jsonl` (+ `archived_sessions`, `.jsonl.zst`) | JSONL | yes | no | **yes**, plan % in log | done |
| OpenCode | `~/.local/share/opencode/opencode.db`, table `message.data` (`tokens`, `cost`, `modelID`); older versions: `storage/message/*/msg_*.json` | SQLite / JSON | yes | yes | no | local |
| OpenWork | runs on OpenCode; its turns land in the same `opencode.db` with `agent: "openwork"` | (via OpenCode) | yes | yes | no | local |
| Kilo CLI | `~/.local/share/kilo/kilo.db` (OpenCode fork) | SQLite | yes | yes | no | ccusage |
| Gemini CLI | `$GEMINI_DATA_DIR` or `~/.gemini/tmp/*/chats/*.json{,l}` | JSON/JSONL | yes | no | no | ccusage |
| Antigravity CLI (`agy`) | `~/.gemini/antigravity{,-cli,-ide}/conversations/*.db`, table `gen_metadata` | SQLite | yes | no | no | ccusage (folder present locally) |
| Qwen Code | `~/.qwen/projects/*/chats/*.jsonl` (`usageMetadata`) | JSONL | yes | no | no | ccusage |
| GitHub Copilot CLI | `$COPILOT_HOME` or `~/.copilot/session-state/*/events.jsonl` | JSONL | yes | AI credits | no | ccusage |
| Amp | `$AMP_DATA_DIR` or `~/.local/share/amp/threads/**/*.json` | JSON | yes | credits | no | ccusage |
| Factory Droid | `~/.factory/sessions/**/*.settings.json` | JSON | yes | no | no | ccusage |
| pi | `$PI_AGENT_DIR` or `~/.pi/agent/sessions/*/*.jsonl` | JSONL | yes | yes | no | ccusage |
| Oh My Pi (`omp`) | `~/.omp/agent/sessions/*/*.jsonl`, `usage` with `cost.total` (pi format) | JSONL | yes | yes | no | local |
| Grok Build CLI | `$GROK_HOME` or `~/.grok/sessions/*/*/updates.jsonl` (`turn_completed`, `costUsdTicks`) | JSONL | yes | yes | no | ccusage |
| Kimi Code | `~/.kimi/sessions/*/*/wire.jsonl`, `~/.kimi-code/sessions/**/wire.jsonl` | JSONL | yes | no | no | ccusage |
| Goose | `~/.local/share/goose/sessions/sessions.db` (or `$GOOSE_PATH_ROOT/data/...`) | SQLite | yes (no cache) | no | no | ccusage |
| Cline (CLI and VS Code ext.) | `~/.cline/data/{tasks,sessions}/...`, `<Code/VSCodium>/User/globalStorage/saoudrizwan.claude-dev/tasks/*/ui_messages.json` | JSON | yes | yes | no | ccusage |
| Hermes Agent | `$HERMES_HOME` or `~/.hermes/state.db` | SQLite | yes | yes | no | ccusage |
| OpenClaw | `~/.openclaw/agents/*/sessions/*.jsonl` (+ old `~/.clawdbot`, `~/.moltbot`) | JSONL / SQLite | yes | yes | no | ccusage |
| Codebuff | `~/.config/manicode/projects/*/chats/*/chat-messages.json` | JSON | yes | credits | no | ccusage |
| ZCode | `~/.zcode/cli/db/db.sqlite`, table `model_usage` | SQLite | yes | no | no | ccusage |
| DeepSeek Harness | `~/.dsh/sessions/*/*/session*.jsonl` (also zstd) | JSONL | yes | no | no | ccusage |
| Crush | per project `.crush/crush.db`, table `sessions` (`prompt_tokens`, `completion_tokens`, `cost`) | SQLite | yes | yes | no | unverified, and per-project, so needs a list of folders |
| Mistral Vibe | `~/.vibe/logs/session/*.json` | JSON | probably | probably | no | unverified |

## 2. Harnesses with no usable token data locally (presence and "working" only)

These keep usage on the server or don't log it. We can still show "working in <project>" from a
recently written session file or a running process, without numbers.

| Agent | What we can read | Status |
|---|---|---|
| Cursor (app and `cursor-agent`) | `~/.cursor/projects/*`, `~/.cursor/chats/*`; usage is server-side | local (folders), unverified (CLI chats) |
| Aider | `.aider.chat.history.md` per repo; cost is printed, not stored structurally | unverified |
| Kiro CLI | `~/.kiro/...` session data, format unknown | unverified |
| Augment (`auggie`), Continue (`cn`), OpenHands CLI, Junie, Warp | formats unknown or server-side | unverified, low priority |

Plain process detection (`/proc/*/comm` or `cmdline` matching `claude`, `codex`, `gemini`, `agy`,
`opencode`, `cursor-agent`, `aider`, `goose`, `amp`, `droid`, `copilot`, `crush`, `kiro-cli`, ...)
is a cheap fallback that works for every harness, and the process cwd gives the project name.

## 3. Meta-harnesses (orchestrators, multiplexers)

Meta-harnesses run the real CLIs above, so their tokens are already counted by section 1.
Counting them again would double the numbers. What they add is **state**: which agent is
working, which one waits for you, which is done. That is better than our current "log written in
the last 90 s" guess, so here they are state sources, not usage sources.

| Meta-harness | Linux | How to read state | Status |
|---|---|---|---|
| herdr | yes | `herdr agent list` / socket `~/.config/herdr/herdr.sock` (JSON, has agent states; `herdr api schema` documents it) | local (installed; server must be running) |
| claude-squad (`cs`) | yes | `~/.claude-squad/state.json` (instances, worktrees, status) | unverified |
| agent-deck | yes | its own state dir, has live status | unverified |
| dmux, pando, agent-of-empires, tuios, Rove | yes | tmux based; state files vary | unverified, low priority |
| vibe-kanban | yes | local web server with an API, tasks and attempts | unverified |
| Happy | yes | CLI wrapper around Claude Code/Codex; state in its daemon | unverified |
| Conductor, cmux, Prowl, Perch, supacode | macOS only | skip | n/a |

### Generic route that covers everything: a status command

Most harnesses now have hooks (Claude Code, Codex `notify`, Gemini CLI, Copilot CLI, Cursor,
Droid, pi/omp extensions, Qwen). Add a CLI verb to the running instance:

```
cornerwindow agent working|waiting|done --agent claude --project "$PWD"
```

and document a one-line hook per harness. Any harness or meta-harness, including ones we never
heard of, can then drive the purple dot and a "waiting for you" state exactly, with no parser.

## Proposed rollout

1. Refactor `platform/agents.py` into a small source registry (one class per agent: `available()`,
   `read(since)` returning `Usage` rows, optional `limits`), keeping the incremental reader. SQLite
   sources open read-only (`mode=ro`) and query only rows newer than the last read.
2. Add the **local** and **ccusage** rows of section 1 (about 20 sources). Show recorded cost or
   credits where the log has them; plan limits stay Claude/Codex only.
3. Add the `cornerwindow agent ...` status verb and herdr as the first meta-harness state source.
4. Process-detection fallback for section 2.
5. Config: `[agents] sources = "auto"` (or a list), so people can hide agents they don't want.
   Card stays one row per agent that was active today, so it doesn't grow with the list.
