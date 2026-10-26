"""Local encrypted model credentials. The master key is never stored in the DB."""
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .errors import ApiError


class ModelSecrets:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _fernet(self, create=False):
        if create and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as file:
                    file.write(Fernet.generate_key())
            except FileExistsError:
                pass
        return Fernet(self.path.read_bytes())

    def encrypt(self, value):
        try:
            return self._fernet(True).encrypt(value.encode()).decode()
        except (OSError, ValueError):
            raise ApiError('MODEL_KEY_UNAVAILABLE', '模型密钥存储不可用。', 503) from None

    def decrypt(self, value):
        if not value:
            return None
        try:
            return self._fernet().decrypt(value.encode()).decode()
        except (OSError, ValueError, InvalidToken):
            raise ApiError('MODEL_KEY_UNAVAILABLE', '模型密钥无法解密，请恢复主密钥或重新输入 API Key。', 503) from None
