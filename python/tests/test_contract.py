"""The contract fixtures: every worker message in `contracts/fixtures/messages/`
validates against the worker's own models (ISC-17, worker side)."""

from pathlib import Path
from typing import get_args

from respec_worker.messages import MESSAGE_ADAPTER, Failed, Reason

MESSAGES_DIR = (
    Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "messages"
)


def test_contract_fixtures_validate() -> None:
    """Every fixture file validates, the folder is not empty, and each failure
    reason has a fixture."""
    files = sorted(MESSAGES_DIR.glob("*.json"))
    assert files, f"no fixtures in {MESSAGES_DIR}"

    messages = [MESSAGE_ADAPTER.validate_json(path.read_bytes()) for path in files]

    reasons = {m.reason for m in messages if isinstance(m, Failed)}
    assert reasons == set(get_args(Reason))
    assert {m.kind for m in messages} == {"started", "done", "failed"}
