"""Sanctioned JSON entry point for canonical events.

Direct calls to ``google.protobuf.json_format`` are forbidden outside this
module. This is the only place the ``preserving_proto_field_name=True``
flag is set, so the snake_case wire format cannot be drifted by accident.
"""

from __future__ import annotations

from typing import TypeVar

from google.protobuf import json_format
from google.protobuf.message import Message

T = TypeVar("T", bound=Message)


def to_json(message: Message) -> str:
    """Serialise a canonical event to its JSON wire form (snake_case keys)."""
    return json_format.MessageToJson(
        message,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=False,
        sort_keys=False,
        indent=None,
    )


def from_json(message_type: type[T], src: str) -> T:
    """Parse a JSON string into the given canonical event message type."""
    msg = message_type()
    json_format.Parse(src, msg, ignore_unknown_fields=True)
    return msg
