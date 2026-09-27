class ComposerError(Exception):
    """Represent a recoverable application error safe to display to a developer.

    Args:
        message: Public description of the failure.
        status: HTTP status code to return.
        diagnostics: Optional credential-redacted inspection details.
    """

    def __init__(
        self, message: str, status: int = 422, diagnostics: dict | None = None
    ):
        super().__init__(message)
        self.status = status
        self.diagnostics = diagnostics
