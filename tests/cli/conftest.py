"""Shared fixtures for CLI tests."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_live_key_probes():
    """Keep CLI tests offline.

    select_params() starts background API-key probes (free HTTP requests to
    the providers) at wizard entry; stub them to report no rejections so
    tests never touch the network regardless of which keys are set locally.
    """
    with patch("kodo.cli._params.probe_keys_async", autospec=True) as probe:
        probe.return_value = lambda timeout=6.0: {}
        yield probe
