"""``KurrentDBEvalSetResultsManager`` — see ``DESIGN.md`` §7.6.

Writes canonical eval events (``EvalRunStarted``, ``TurnScored``,
``EvalRunCompleted``; ``SCHEMA.md`` §3.5) to ``EvalRun-{run_id}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.evaluation.eval_set_results_manager import EvalSetResultsManager

if TYPE_CHECKING:  # pragma: no cover
    from google.adk.evaluation.eval_result import EvalCaseResult, EvalSetResult
    from kurrentdbclient import AsyncKurrentDBClient


class KurrentDBEvalSetResultsManager(EvalSetResultsManager):
    """KurrentDB-backed eval-results manager emitting canonical events."""

    def __init__(self, client: AsyncKurrentDBClient) -> None:
        self._client = client

    def save_eval_set_result(
        self,
        app_name: str,
        eval_set_id: str,
        eval_case_results: list[EvalCaseResult],
    ) -> None:
        raise NotImplementedError

    def get_eval_set_result(
        self, app_name: str, eval_set_result_id: str
    ) -> EvalSetResult:
        raise NotImplementedError

    def list_eval_set_results(self, app_name: str) -> list[str]:
        raise NotImplementedError
