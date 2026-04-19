# Claude Agent SDK BasicAgent sample

End-to-end demo of `KurrentDBSessionStore` for the Claude Agent SDK. Two turns sharing one `session_id`:

- Turn 1: a fresh session. Claude answers; the CLI writes its local JSONL transcript, and our `SessionStore.append` mirrors every line to KurrentDB.
- Turn 2: `ClaudeAgentOptions(resume=session_id, ...)`. The SDK calls `store.load(key)`; our adapter reconstructs the transcript from KurrentDB; the SDK materialises it to a temp JSONL file so the CLI can resume from it. Claude's answer references the first turn's context without any other hand-holding.

A type-count summary of what actually landed in the `AgentSession-{session_id}` stream prints at the end.

## How this differs from the other samples

The Claude Agent SDK is a subprocess wrapper around the Claude Code CLI. The CLI owns conversation state on local disk under `CLAUDE_CONFIG_DIR`; our `SessionStore` is a **post-hoc mirror**, not the source of truth. That makes resume work exactly the way the CLI's native `--resume` flag does — the SDK materialises a temp JSONL from our store's `load()` and hands it to the subprocess.

See [`DESIGN.md`](../../DESIGN.md) §2 for the architectural details.

## Prerequisites

- KurrentDB running:

  ```bash
  docker compose up -d
  ```

- Claude Code CLI installed and authenticated. Install per the [official instructions](https://docs.claude.com/en/docs/claude-code/quickstart). Authenticate with one of:

  ```bash
  claude login                          # OAuth
  export ANTHROPIC_API_KEY=sk-ant-...   # API-key auth
  ```

- Package installed:

  ```bash
  pip install -e '.[dev]'
  ```

## Run

From `claude-agent-sdk/python/`:

```bash
python -m samples.basic_agent.main
```

## What this demonstrates

- **`SessionStore.append` wiring** — `ClaudeAgentOptions(session_store=KurrentDBSessionStore(...))` is the only change needed to mirror every CLI transcript line to KurrentDB.
- **`SessionStore.load` wiring** — setting `resume=session_id` triggers the SDK's store-backed resume path. Our adapter returns the entries from KurrentDB; the CLI resumes as if it were reading its own local file.
- **Lossless round-trip** — each CLI JSONL line is stored verbatim as a `ClaudeSDKEntry` event and reconstructed byte-equal-via-JSON on `load`, per the SDK's single required invariant.
- **Stream naming parity** — the main transcript lands in `AgentSession-{session_id}`, the same canonical prefix every other integration in the monorepo uses.

## Troubleshooting

- **"No such command: claude"** — install the Claude Code CLI; the SDK spawns it as a subprocess.
- **Auth prompt or `credentials not found` errors** — run `claude login` once, or set `ANTHROPIC_API_KEY`.
- **`Total events` is 0** — the append batches run asynchronously; if the script exits before the subprocess flushes, entries may not have landed yet. Add a small `await asyncio.sleep(0.5)` before the stream dump if it's a race in practice.
