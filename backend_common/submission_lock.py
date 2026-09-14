"""One synchronous boundary for runtime admission and scope deletion."""

import inspect
import threading
from functools import wraps

submission_lock = threading.RLock()


def serialize_submission(function):
    """Hold admission across persistence and enqueue, before competing runs become visible."""
    if inspect.iscoroutinefunction(function):
        raise TypeError("Submission admission must wrap a synchronous function")

    @wraps(function)
    def guarded(*args, **kwargs):
        with submission_lock:
            return function(*args, **kwargs)

    return guarded
