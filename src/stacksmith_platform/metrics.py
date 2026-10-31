import math
from collections import deque
from threading import Lock


class TimingSamples:
    """Keep bounded process-local duration samples and cumulative counters.

    Args:
        capacity: Maximum number of recent observations retained for quantiles.
    """

    def __init__(self, capacity=1024):
        self._samples = deque(maxlen=capacity)
        self._lock = Lock()
        self._count = 0
        self._total = 0.0
        self._maximum = 0.0

    def observe(self, seconds):
        """Record a duration without retaining request data.

        Args:
            seconds: Measured elapsed time in seconds.

        Returns:
            None.
        """
        with self._lock:
            self._samples.append(seconds)
            self._count += 1
            self._total += seconds
            self._maximum = max(self._maximum, seconds)

    def snapshot(self):
        """Read cumulative totals and quantiles over the retained sample window.

        Returns:
            Non-sensitive counters, seconds, and recent-window quantiles.
        """
        with self._lock:
            samples = sorted(self._samples)
            return {
                "count": self._count,
                "total_seconds": self._total,
                "max_seconds": self._maximum,
                "sample_count": len(samples),
                "sample_capacity": self._samples.maxlen,
                "p50_seconds": samples[math.ceil(len(samples) * 0.5) - 1]
                if samples
                else None,
                "p95_seconds": samples[math.ceil(len(samples) * 0.95) - 1]
                if samples
                else None,
            }
