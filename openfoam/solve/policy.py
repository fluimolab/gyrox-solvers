"""RunManifest executor policy transcription; deliberately has no defaults."""
from __future__ import annotations

from typing import Any


def executor_policy(request: dict[str, Any], run_manifest: dict[str, Any]) -> dict[str, Any]:
    policy = dict(run_manifest["executorPolicy"])
    requested = request["limits"]["checkpointIntervalSec"]
    if requested != policy["checkpointIntervalSec"]:
        raise ValueError("request checkpoint interval does not match executor policy")
    return policy
