from __future__ import annotations

import pytest

from openfoam.solve.policy import executor_policy


@pytest.mark.parametrize("_case", [None], ids=["[SR-13]"])
def test_policy_transcription(_case):
    request = {"limits": {"checkpointIntervalSec": 900}}
    manifest = {"executorPolicy": {"maxWallClockSec": 100, "resourceClass": "small", "checkpointIntervalSec": 900, "judgmentSlackIters": 50}}
    assert executor_policy(request, manifest) == manifest["executorPolicy"]
    with pytest.raises(KeyError):
        executor_policy({"limits": {}}, manifest)
