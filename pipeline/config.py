"""vm configuration (config.json and .env at the project root).

Input folder priority: env VM_INPUT_DIR (one-off override)
> config.json > default `input/`. config.json holds only deviations from
the defaults — it's a machine/session setting, not a project decision, so it
is not archived (archive moves only output/).

`.env` (gitignored, also outside archiving) holds machine secrets —
e.g. STABILITY_API_KEY for `vm music`; loaded by `load_env()` at CLI startup,
the real env always wins over the file.
"""

import json
import os
from pathlib import Path

from . import schema

CONFIG_PATH = Path("config.json")
ENV_PATH = Path(".env")
DEFAULTS = {"input_dir": "input"}


def load_env(path: Path = ENV_PATH) -> None:
    """`KEY=value` lines from .env into os.environ; existing env is not
    overwritten (one-off overrides like VM_INPUT_DIR keep working as before)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def save(cfg: dict) -> None:
    """Writes only deviations from the defaults; with none, removes the file."""
    diff = {k: v for k, v in cfg.items() if DEFAULTS.get(k) != v}
    if diff:
        schema.check(diff, "config", str(CONFIG_PATH))
        CONFIG_PATH.write_text(
            json.dumps(diff, indent=2, ensure_ascii=False) + "\n")
    elif CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def input_dir() -> Path:
    env = os.environ.get("VM_INPUT_DIR")
    return Path(env) if env else Path(load()["input_dir"])


def input_dir_source() -> str:
    if os.environ.get("VM_INPUT_DIR"):
        return "env VM_INPUT_DIR"
    if "input_dir" in (json.loads(CONFIG_PATH.read_text())
                       if CONFIG_PATH.exists() else {}):
        return "config.json"
    return "default"
