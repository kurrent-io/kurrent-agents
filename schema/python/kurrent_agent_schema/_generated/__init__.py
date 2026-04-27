# Generated protobuf code lives under `kurrent/agent/v2/` inside this package.
# The protoc-emitted Python files cross-import each other via the proto package
# path (e.g. `from kurrent.agent.v2 import value_types_pb2`), which only
# resolves if this directory is on `sys.path`. Add it so that importing
# `kurrent_agent_schema._generated.kurrent.agent.v2.events_pb2` succeeds
# regardless of how the consumer set up their environment.
import os as _os
import sys as _sys

_HERE = _os.path.dirname(__file__)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
