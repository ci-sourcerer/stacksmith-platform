import time
from urllib.parse import quote

import httpx
import jwt

from .errors import ComposerError


class GitHubProvider:
    """Use installation tokens independently of browser session lifetime.

    Args:
        settings: Administrator-managed GitHub App settings.
    """

    def __init__(self, settings):
        self.settings = settings

    def installation_token(self, installation: int) -> str:
        """Issue a short-lived token for an approved installation.

        Args:
            installation: GitHub installation identifier.

        Returns:
            Installation access token.

        Raises:
            ComposerError: GitHub rejects the installation or is unavailable.
        """
        response = httpx.post(
            f"https://api.github.com/app/installations/{installation}/access_tokens",
            headers=self._app_headers(),
            timeout=20,
        )
        if response.status_code != 201:
            raise ComposerError("GitHub installation access is unavailable.", 503)
        return response.json()["token"]

    def _app_headers(self):
        return {
            "Authorization": f"Bearer {jwt.encode({'iat': int(time.time()) - 30, 'exp': int(time.time()) + 540, 'iss': self.settings.github_app_id}, self.settings.github_private_key, algorithm='RS256')}",
            "Accept": "application/vnd.github+json",
        }

    def app(self) -> dict:
        """Read the configured App's public identity.

        Returns:
            GitHub App metadata, including its installation slug.

        Raises:
            ComposerError: GitHub rejects the configured App credentials.
        """
        return self._app_request("/app")

    def _app_request(self, path, params=None):
        response = httpx.get(
            f"https://api.github.com{path}",
            headers=self._app_headers(),
            params=params,
            timeout=20,
        )
        if response.is_error:
            raise ComposerError(
                "GitHub App discovery is unavailable. Retry later.", 502
            )
        return response.json()

    def _installation_pages(self, installation):
        for page in range(1, 101):
            payload = self.request(
                installation,
                "GET",
                "/installation/repositories",
                params={"per_page": 100, "page": page},
            )["repositories"]
            yield from payload
            if len(payload) < 100:
                return
        raise ComposerError("Repository discovery exceeded its page limit.", 503)

    def catalog(self) -> list[dict]:
        """List repositories from every active installation of this App.

        Returns:
            Repository metadata paired with authoritative installation IDs.

        Raises:
            ComposerError: GitHub discovery fails or exceeds its page limit.
        """
        result = []
        for page in range(1, 101):
            installations = self._app_request(
                "/app/installations", {"per_page": 100, "page": page}
            )
            for installation in installations:
                if not installation.get("suspended_at"):
                    result.extend(
                        {**repository, "installation_id": installation["id"]}
                        for repository in self._installation_pages(installation["id"])
                    )
            if len(installations) < 100:
                return result
        raise ComposerError("Installation discovery exceeded its page limit.", 503)

    def repository_installation(self, repository: str) -> int:
        """Discover this App's installation for a repository.

        Args:
            repository: Validated GitHub owner/repository name.

        Returns:
            Installation ID owned by the configured GitHub App.

        Raises:
            ComposerError: The App is not installed or lookup is unavailable.
        """
        response = httpx.get(
            f"https://api.github.com/repos/{repository}/installation",
            headers=self._app_headers(),
            timeout=20,
        )
        if response.status_code == 404:
            raise ComposerError(
                "Install the configured GitHub App on this repository and include it in the installation's repository access.",
                422,
            )
        if response.is_error:
            raise ComposerError(
                "GitHub App installation lookup failed. Check the App configuration and retry.",
                502,
            )
        return int(response.json()["id"])

    def request(self, installation: int, method: str, path: str, **kwargs):
        """Call GitHub using a fresh installation credential.

        Args:
            installation: Approved installation identifier.
            method: HTTP method.
            path: GitHub API path.
            **kwargs: HTTP request options.

        Returns:
            Decoded GitHub response.

        Raises:
            ComposerError: GitHub rejects the request.
        """
        response = httpx.request(
            method,
            f"https://api.github.com{path}",
            headers={
                "Authorization": f"Bearer {self.installation_token(installation)}",
                "Accept": "application/vnd.github+json",
            },
            timeout=20,
            **kwargs,
        )
        if response.is_error:
            raise ComposerError(
                f"GitHub rejected repository request {path}.",
                404 if response.status_code == 404 else 403,
            )
        return response.json()

    def revision(self, repository, reference: str):
        """Resolve a commit or branch through an approved installation.

        Args:
            repository: Registered repository record.
            reference: Commit or branch name.

        Returns:
            GitHub commit details.

        Raises:
            ComposerError: The revision is inaccessible.
        """
        return self.request(
            repository["installation"],
            "GET",
            f"/repos/{repository['id']}/commits/{quote(reference, safe='')}",
        )
