"""Schema contract for persistent JSON artifacts (pipeline/schemas/*.schema.json).

Single source of truth for the shapes of output/project.json ("project"),
output/<stem>/summary.json ("summary") and config.json ("config"), and for the
closed tag vocabularies — montage.py reads SHOTS/ROLES from here via tag_enum().
Draft 2020-12; enforced by the writers' save hooks and by `vm validate`.
"""

import functools
import json
from importlib import resources

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match


class SchemaError(ValueError):
    """Data violates the contract; message lists the json-paths of the errors."""


@functools.cache
def get(name: str) -> dict:
    """Loaded schema by stem: "project" | "summary" | "config"."""
    ref = resources.files("pipeline") / "schemas" / f"{name}.schema.json"
    return json.loads(ref.read_text())


@functools.cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(get(name))


def errors(data, name: str) -> list[str]:
    """Readable violations ('$.selects[3].tags.shot: ...'); empty list = valid."""
    errs = sorted(_validator(name).iter_errors(data), key=lambda e: e.json_path)
    # best_match descends into anyOf branches — the enum violation, not
    # "not valid under any of the given schemas"
    best = (best_match([e]) or e for e in errs)
    return [f"{e.json_path}: {e.message}" for e in best]


def check(data, name: str, source: str) -> None:
    """Raises SchemaError (first 5 violations) — `source` names the artifact."""
    errs = errors(data, name)
    if errs:
        head = errs[:5]
        if len(errs) > len(head):
            head.append(f"... and {len(errs) - len(head)} more")
        raise SchemaError(f"{source} violates the {name} schema:\n  "
                          + "\n  ".join(head))


def tag_enum(field: str) -> tuple[str, ...]:
    """Closed tag vocabulary from the project schema, e.g. tag_enum("shot")."""
    return tuple(get("project")["$defs"]["tags"]["properties"][field]["enum"])
