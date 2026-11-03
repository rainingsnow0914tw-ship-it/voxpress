from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_modifier_mask(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Never inject a real mask key on the (Windows) CI runner.

    Returns a list that grows by one per mask the hook would have sent.
    """

    sent: list[int] = []
    monkeypatch.setattr("voxpress.winkeys.send_modifier_mask", lambda: sent.append(1) or True)
    return sent
