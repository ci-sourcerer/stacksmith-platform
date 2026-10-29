import pytest

from stacksmith_platform.metrics import TimingSamples
from stacksmith_platform.settings import Settings


def test_duration_quantiles_are_bounded_while_totals_remain_cumulative():
    samples = TimingSamples(capacity=3)
    for seconds in (1, 2, 3, 4):
        samples.observe(seconds)
    assert samples.snapshot() == {
        "count": 4,
        "total_seconds": 10,
        "max_seconds": 4,
        "sample_count": 3,
        "sample_capacity": 3,
        "p50_seconds": 3,
        "p95_seconds": 4,
    }


@pytest.mark.parametrize(
    "options",
    [
        {"database_pool_size": 0},
        {"database_pool_timeout": 0},
        {"database_listener_url": "sqlite:///test"},
        {"database_listener_url": "postgresql://localhost"},
    ],
)
def test_invalid_connection_budgets_and_listener_urls_are_rejected(options):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **options)
