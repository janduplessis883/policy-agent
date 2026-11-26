import hmac
import time
from threading import Lock


class PinGuard:
    """Process-wide rate limit: five failures lock every session on this worker for one minute.

    This is not per-person login. Individual accounts (for example Streamlit OIDC) are not used.
    """

    def __init__(self, max_attempts=5, cooldown=60):
        self.max_attempts = max_attempts
        self.cooldown = cooldown
        self.failures = []
        self.blocked_until = 0
        self.lock = Lock()

    def verify(self, supplied: str, expected: str, now=None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            if now < self.blocked_until:
                return False
            self.failures = [t for t in self.failures if now - t < self.cooldown]
            if expected and supplied and hmac.compare_digest(supplied.encode(), expected.encode()):
                self.failures.clear()
                return True
            self.failures.append(now)
            if len(self.failures) >= self.max_attempts:
                self.blocked_until = now + self.cooldown
            return False
