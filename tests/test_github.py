import json

import httpx
import pytest

from stacksmith_platform.errors import ComposerError
from stacksmith_platform.github import GitHubClient


def _transport(handler):
    return httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )


class GitHubFixture:
    """Model GitHub's branch and pull-request APIs for write-path tests."""

    def __init__(self):
        self.requests = []
        self.current_head = "base-sha"
        self.writable = True
        self.existing_pr = False

    def handle(self, request):
        """Return the API response for a recorded request."""
        self.requests.append(request)
        path = request.url.path
        if path == "/repos/org/stacks":
            payload = {
                "permissions": {"push": self.writable},
            }
        elif path.endswith("/pulls"):
            if request.method == "GET":
                payload = (
                    [{"html_url": "https://github.com/org/stacks/pull/7", "number": 7}]
                    if self.existing_pr
                    else []
                )
            else:
                self.existing_pr = True
                payload = {
                    "html_url": "https://github.com/org/stacks/pull/7",
                    "number": 7,
                }
        elif path.endswith("/commits/main"):
            payload = {"sha": self.current_head}
        elif "/matching-refs/" in path:
            payload = []
        elif path.endswith("/git/commits/base-sha"):
            payload = {"tree": {"sha": "base-tree"}}
        elif path.endswith("/git/trees"):
            payload = {"sha": "new-tree"}
        elif path.endswith("/git/commits"):
            payload = {"sha": "new-commit"}
        elif path.endswith("/git/refs"):
            payload = {"ref": "created"}
        else:
            raise AssertionError(f"Unexpected request: {request.method} {path}")
        return httpx.Response(200, json=payload)


SNAPSHOT = {
    "repository": "org/stacks",
    "principal": "developer",
    "head": "base-sha",
    "branch": "main",
    "path": "apps/storefront/stack.yaml",
}


def test_pr_writes_exactly_one_file_to_a_new_branch_and_retries_reuse_pr():
    fixture = GitHubFixture()
    client = GitHubClient("test-token", _transport(fixture.handle))
    source = "name: changed\ncomponents: {}\n"
    first = client.create_pr(SNAPSHOT, source)
    request_count = len(fixture.requests)
    assert client.create_pr(SNAPSHOT, source) == first
    assert all(request.method == "GET" for request in fixture.requests[request_count:])
    writes = [request for request in fixture.requests if request.method == "POST"]
    assert len(writes) == 4
    tree = json.loads(writes[0].content)
    assert tree == {
        "base_tree": "base-tree",
        "tree": [
            {
                "path": SNAPSHOT["path"],
                "mode": "100644",
                "type": "blob",
                "content": source,
            }
        ],
    }
    assert json.loads(writes[2].content)["ref"].startswith("refs/heads/composer/")
    assert json.loads(writes[3].content)["draft"] is False
    assert first["patch_url"] == f"{first['url']}.patch"
    assert all(
        request.headers["authorization"] == "Bearer test-token"
        for request in fixture.requests
    )


def test_pr_can_be_created_as_a_draft():
    fixture = GitHubFixture()
    GitHubClient("token", _transport(fixture.handle)).create_pr(
        SNAPSHOT, "changed", draft=True
    )
    writes = [request for request in fixture.requests if request.method == "POST"]
    assert json.loads(writes[-1].content)["draft"] is True


@pytest.mark.parametrize("environment", [None, "dev"])
def test_pr_records_inspection_context_and_uses_it_for_retry_identity(environment):
    fixture = GitHubFixture()
    client = GitHubClient("token", _transport(fixture.handle))
    context = {
        "mode": "deployment" if environment else "stack_preview",
        "environment": environment,
        "repository": SNAPSHOT["repository"],
        "revision": SNAPSHOT["head"],
        "path": SNAPSHOT["path"],
        "config_revision": "config-one",
    }
    client.create_pr(SNAPSHOT, "changed", inspection_context=context)
    body = json.loads(fixture.requests[-1].content)["body"]
    assert json.dumps(context, indent=2, sort_keys=True) in body
    assert "Deployment planning remains separate" in body
    fixture.existing_pr = False
    client.create_pr(
        SNAPSHOT,
        "changed",
        inspection_context={**context, "config_revision": "config-two"},
    )
    heads = [
        request.url.params["head"]
        for request in fixture.requests
        if request.method == "GET" and request.url.path.endswith("/pulls")
    ]
    assert len(set(heads)) == 2


def test_changed_base_prevents_all_writes():
    fixture = GitHubFixture()
    fixture.current_head = "advanced-sha"
    with pytest.raises(ComposerError, match="base branch changed"):
        GitHubClient("token", _transport(fixture.handle)).create_pr(SNAPSHOT, "changed")
    assert all(request.method == "GET" for request in fixture.requests)


def test_lost_write_access_prevents_all_writes():
    fixture = GitHubFixture()
    fixture.writable = False
    with pytest.raises(ComposerError, match="write access"):
        GitHubClient("token", _transport(fixture.handle)).create_pr(SNAPSHOT, "changed")
    assert all(request.method == "GET" for request in fixture.requests)


class DiscoveryFixture:
    """Return repositories visible through two installations, including duplicates."""

    def handle(self, request):
        """Serve discovery fixtures without network access."""
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 1}, {"id": 2}]})
        if request.url.path.endswith("/properties/values"):
            return httpx.Response(200, json=[])
        if request.url.path.startswith("/repos/"):
            return httpx.Response(200, json={"topics": []})
        return httpx.Response(
            200,
            json={
                "repositories": [
                    {
                        "id": 1,
                        "full_name": "org/stacks",
                        "default_branch": "main",
                        "permissions": {"push": True},
                    },
                    {
                        "id": 2,
                        "full_name": "org/unrelated",
                        "default_branch": "main",
                    },
                    {
                        "id": 3,
                        "full_name": "org/archived",
                        "default_branch": "main",
                        "archived": True,
                        "permissions": {"push": True},
                    },
                ]
            },
        )


def test_discovery_lists_repositories_deduplicates_and_marks_archived_read_only():
    repositories = GitHubClient(
        "token", _transport(DiscoveryFixture().handle)
    ).repositories()
    assert [repo["name"] for repo in repositories] == [
        "org/archived",
        "org/stacks",
        "org/unrelated",
    ]
    assert repositories[0]["writable"] is False
    assert repositories[1]["writable"] is True


@pytest.mark.parametrize(
    ("repository", "expected"),
    [
        ("org/stacks", ["org/stacks"]),
        ("ORG/STACKS", ["org/stacks"]),
        ("org/absent", []),
    ],
)
def test_targeted_discovery_only_enriches_matching_membership(repository, expected):
    fixture = DiscoveryFixture()
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return fixture.handle(request)

    assert [
        item["name"]
        for item in GitHubClient("token", _transport(handle)).repositories(repository)
    ] == expected
    assert [path for path in requests if path.startswith("/repos/")] == (
        ["/repos/org/stacks", "/repos/org/stacks/properties/values"] if expected else []
    )


@pytest.mark.parametrize("paginated", ["installations", "repositories"])
def test_targeted_discovery_searches_later_pages(paginated):
    fixture = DiscoveryFixture()
    requests = []

    def handle(request):
        requests.append((request.url.path, request.url.params.get("page")))
        page = int(request.url.params.get("page", "1"))
        if request.url.path == "/user/installations":
            if paginated == "installations" and page == 1:
                return httpx.Response(
                    200,
                    json={"installations": [{"id": index} for index in range(1, 101)]},
                )
            return httpx.Response(200, json={"installations": [{"id": 101}]})
        if request.url.path.startswith("/user/installations/"):
            if paginated == "repositories" and page == 1:
                return httpx.Response(
                    200,
                    json={
                        "repositories": [
                            {"full_name": f"org/other-{index}"} for index in range(100)
                        ]
                    },
                )
            if request.url.path != "/user/installations/101/repositories":
                return httpx.Response(200, json={"repositories": []})
        return fixture.handle(request)

    assert [
        item["name"]
        for item in GitHubClient("token", _transport(handle)).repositories("org/stacks")
    ] == ["org/stacks"]
    assert (
        "/user/installations"
        if paginated == "installations"
        else "/user/installations/101/repositories",
        "2",
    ) in requests


def test_targeted_discovery_preserves_custom_properties():
    fixture = DiscoveryFixture()

    def handle(request):
        if request.url.path == "/repos/org/stacks/properties/values":
            return httpx.Response(
                200, json=[{"property_name": "stacksmith", "value": "stack"}]
            )
        return fixture.handle(request)

    assert GitHubClient("token", _transport(handle)).repositories("org/stacks")[0][
        "custom_properties"
    ] == {"stacksmith": "stack"}


@pytest.mark.parametrize("status", [403, 404])
def test_targeted_discovery_checks_current_repository_access(status):
    fixture = DiscoveryFixture()

    def handle(request):
        if request.url.path == "/repos/org/stacks":
            return httpx.Response(status)
        return fixture.handle(request)

    if status == 403:
        with pytest.raises(ComposerError, match="access was denied"):
            GitHubClient("token", _transport(handle)).repositories("org/stacks")
    else:
        assert (
            GitHubClient("token", _transport(handle)).repositories("org/stacks") == []
        )


@pytest.mark.parametrize("repository", [None, "org/stacks"])
def test_personal_installation_uses_topics_without_requesting_custom_properties(
    repository,
):
    fixture = DiscoveryFixture()

    def handle(request):
        if request.url.path == "/user/installations":
            return httpx.Response(
                200, json={"installations": [{"id": 1, "account": {"type": "User"}}]}
            )
        assert not request.url.path.endswith("/properties/values")
        if request.url.path.startswith("/repos/"):
            return httpx.Response(200, json={"topics": ["stacksmith-stack"]})
        return fixture.handle(request)

    repositories = GitHubClient("token", _transport(handle)).repositories(repository)
    assert len(repositories) == (3 if repository is None else 1)
    assert all(item["topics"] == ["stacksmith-stack"] for item in repositories)
    assert all(item["custom_properties"] == {} for item in repositories)


@pytest.mark.parametrize("repository", [None, "org/stacks"])
def test_unavailable_custom_properties_do_not_block_topic_discovery(repository):
    fixture = DiscoveryFixture()

    def handle(request):
        if request.url.path.endswith("/properties/values"):
            return httpx.Response(404)
        if request.url.path.startswith("/repos/"):
            return httpx.Response(200, json={"topics": ["stacksmith-stack"]})
        return fixture.handle(request)

    repositories = GitHubClient("token", _transport(handle)).repositories(repository)
    assert len(repositories) == (3 if repository is None else 1)
    assert all(item["custom_properties"] == {} for item in repositories)
    assert all(item["topics"] == ["stacksmith-stack"] for item in repositories)


def test_disappearing_repository_does_not_block_other_repositories():
    fixture = DiscoveryFixture()

    def handle(request):
        if request.url.path == "/repos/org/archived":
            return httpx.Response(404)
        return fixture.handle(request)

    assert [
        item["name"]
        for item in GitHubClient("token", _transport(handle)).repositories()
    ] == ["org/stacks", "org/unrelated"]


def test_custom_property_authentication_failures_still_surface():
    fixture = DiscoveryFixture()

    def handle(request):
        if request.url.path.endswith("/properties/values"):
            return httpx.Response(403)
        return fixture.handle(request)

    with pytest.raises(ComposerError, match="access was denied"):
        GitHubClient("token", _transport(handle)).repositories()
