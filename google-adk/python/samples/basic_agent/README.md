# BasicAgent sample

End-to-end demo of `KurrentDBSessionService`: a conversational agent backed by Anthropic Claude, with chat history persisted to KurrentDB.

Two turns across two `Runner` instances, sharing one `session_id`. The second Runner simulates a new process picking up the conversation:

- Turn 1: "What's the weather in Tokyo?" — the agent calls the `get_weather` tool.
- Turn 2 (new Runner): "What temperature did you just tell me?" — the persisted history lets the agent answer without a second tool call.

Afterwards the script prints every event that landed in KurrentDB, including token-usage counters.

## Prerequisites

- KurrentDB running locally:

  ```bash
  docker compose up -d    # from the python/ folder
  ```

- Anthropic API key:

  ```bash
  export ANTHROPIC_API_KEY=sk-ant-...
  ```

- The package installed in a dev venv:

  ```bash
  pip install -e '.[dev]' 'google-adk[extensions]'
  ```

## Run

From the `google-adk/python/` directory:

```bash
python -m samples.basic_agent.main
```

Expected output (abridged):

```
Session id: alice-weather-ab12cd34

=== Turn 1 (fresh session) ===
User:  What's the weather in Tokyo?
Agent: It's 22°C and sunny in Tokyo right now.

=== Turn 2 (resumed session, new Runner instance) ===
User:  What temperature did you just tell me?
Agent: I told you it was 22°C in Tokyo.

=== Persisted events ===
  [0] user: text='What's the weather in Tokyo?'
  [1] basic_agent: fn_call=get_weather({'city': 'Tokyo'}) | usage=in:512 out:24
  [2] basic_agent: fn_response=get_weather={'status': 'success', ...}
  [3] basic_agent: text='It's 22°C and sunny in Tokyo right now.' | usage=in:540 out:18
  [4] user: text='What temperature did you just tell me?'
  [5] basic_agent: text='I told you it was 22°C in Tokyo.' | usage=in:620 out:12

Total persisted events: 6
```

## What this demonstrates

- **Session persistence** via the canonical `AgentSession-{session_id}` stream.
- **Tool calls** (function call + function response) round-trip through the codec.
- **Token usage** captured as `$usage` KurrentDB event metadata on every assistant event.
- **Cross-Runner resume** — no in-memory state carries between Runner instances; the second Runner reads the full history from KurrentDB.
