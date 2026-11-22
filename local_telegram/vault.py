"""Store the Windows GUI's MTProto session with current-user DPAPI."""
import json
from pathlib import Path
import tempfile

FILENAME = "account.dpapi"


def load(directory):
    data = read_protected(directory / FILENAME)
    if data is not None and (not isinstance(data["api_id"], int) or not data["api_hash"] or not data["session"]):
        raise ValueError("Invalid saved login")
    return data


def read_protected(path):
    if not path.exists():
        return None
    import win32crypt

    _, raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)
    return json.loads(raw)


def save(directory, api_id, api_hash, session):
    if not session:
        raise ValueError("Missing authorization key")
    write_protected(directory / FILENAME, dict(api_id=api_id, api_hash=api_hash, session=session))


def write_protected(path, data):
    import win32crypt

    raw = json.dumps(data).encode()
    blob = win32crypt.CryptProtectData(raw, "Crypto Cash Hunter", None, None, None, 1)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(blob)
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
