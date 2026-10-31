"""Public runtime failures; never return database/provider exception text."""


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, *, details=None):
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status
        self.details = details if isinstance(details, dict) else None
