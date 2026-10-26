"""Enable loading the custom integration under test."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield

# The aiohttp/pycares DNS resolver starts a daemon thread at import time
# (Thread-1 (_run_safe_shutdown_loop)); the HA test harness's teardown
# rejects any leftover thread by name. Filter exactly that one out so the
# cleanup check still catches real leaks from the integration itself.
import threading


@pytest.fixture(autouse=True)
def _tolerate_pycares_shutdown_thread(monkeypatch):
    real_enumerate = threading.enumerate

    def enumerate_without_pycares():
        return [t for t in real_enumerate() if "safe_shutdown_loop" not in t.name]

    monkeypatch.setattr(threading, "enumerate", enumerate_without_pycares)
    yield
