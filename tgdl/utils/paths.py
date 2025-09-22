from pathlib import Path

def ensure_dir(p: str | Path) -> Path:
    path = Path(p).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
