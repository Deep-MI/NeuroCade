"""Check that background release checks stop promptly on application shutdown."""

import threading

from api_service.jobs import update_checker


def test_update_checker_stops_without_waiting_for_the_interval(monkeypatch):
    checked = threading.Event()
    monkeypatch.setattr(update_checker, "check_once", lambda _values: checked.set())
    checker = update_checker.start_update_checker()
    try:
        assert checked.wait(5)
    finally:
        checker.stop()
    assert not checker.thread.is_alive()
