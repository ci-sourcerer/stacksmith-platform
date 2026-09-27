from stacksmith_platform.repository_filter import DEFAULT_FILTER, filtered, matches


def test_default_filter_accepts_property_or_topic():
    assert matches({"custom_properties": {"stacksmith": "stack"}}, DEFAULT_FILTER)
    assert matches({"topics": ["stacksmith-stack"]}, DEFAULT_FILTER)
    assert not matches({"topics": ["unrelated"]}, DEFAULT_FILTER)


def test_filter_matches_property_values_without_case_sensitivity():
    settings = {"properties": {"team": "platform"}, "topics": []}

    assert matches({"custom_properties": {"team": "Platform"}}, settings)


def test_filter_returns_only_matching_repositories():
    repositories = [
        {"name": "org/stack", "topics": ["stacksmith-stack"]},
        {"name": "org/other", "topics": ["unrelated"]},
    ]

    assert [item["name"] for item in filtered(repositories, DEFAULT_FILTER)] == [
        "org/stack"
    ]
