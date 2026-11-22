"""Store the Windows GUI's MTProto session with current-user DPAPI."""
import json
from pathlib import Path
import tempfile

FILENAME = "account.dpapi"


def load(directory):
    path = directory / FILENAME
    if not path.exists():
        return None
    import win32crypt

    _, raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)
    data = json.loads(raw)
    if not isinstance(data["api_id"], int) or not data["api_hash"] or not data["session"]:
        raise ValueError("Invalid saved login")
    return data


def save(directory, api_id, api_hash, session):
    import win32crypt

    if not session:
        raise ValueError("Missing authorization key")
    raw = json.dumps(dict(api_id=api_id, api_hash=api_hash, session=session)).encode()
    blob = win32crypt.CryptProtectData(raw, "Crypto Cash Hunter", None, None, None, 1)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(blob)
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(directory / FILENAME)
    finally:
        temp.unlink(missing_ok=True)
