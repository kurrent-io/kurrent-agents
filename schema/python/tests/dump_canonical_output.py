"""Round-trip every fixture through to_json() and write the result to a
disk path. The companion .NET program does the same. CI structurally
diffs the two output directories.

Usage: uv run python tests/dump_canonical_output.py <output_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from kurrent_agent_schema import TokenUsage, from_json, to_json
from kurrent_agent_schema.registry import EVENT_TYPE_BY_NAME

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: dump_canonical_output.py <output_dir>")
    out_root = Path(sys.argv[1])
    (out_root / "events").mkdir(parents=True, exist_ok=True)
    (out_root / "metadata").mkdir(parents=True, exist_ok=True)

    for path in sorted((FIXTURES / "events").glob("*.json")):
        cls = EVENT_TYPE_BY_NAME[path.stem]
        parsed = from_json(cls, path.read_text())
        (out_root / "events" / path.name).write_text(to_json(parsed))

    usage_path = FIXTURES / "metadata" / "usage.json"
    parsed = from_json(TokenUsage, usage_path.read_text())
    (out_root / "metadata" / "usage.json").write_text(to_json(parsed))


if __name__ == "__main__":
    main()
