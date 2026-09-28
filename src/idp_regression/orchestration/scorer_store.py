"""Where custom scorer specs live on disk, and how a run gets them.

The compiler is `classifier/custom.py` and is pure (INV-02). This module
is the half that touches the filesystem and the registry: one JSON file
per scorer under `SCORER_DIR`, loaded and registered by an EXPLICIT call.

A spec carries **no extracted values** -- it is gate configuration, not
run data -- so unlike every other directory these tools write, this one
is safe to commit, and committing it is what makes a custom gate
reviewable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from idp_regression.classifier.custom import (
    NAME_PATTERN,
    ScorerSpec,
    SpecError,
    build_classifier,
    parse_spec,
    verify_monotone,
)
from idp_regression.classifier.registry import CLASSIFIERS

#: Where specs are kept, relative to the working directory. Safe to
#: commit: a spec holds rules, never values.
SCORER_DIR: Final = Path(".idp-regression-scorers")


def spec_path(name: str, directory: Path | None = None) -> Path:
    """The file a spec lives in.

    `name` has already passed `NAME_PATTERN` (kebab-case, no dot, no
    slash) by the time it reaches here, so it cannot traverse; the
    `resolve`/parent check below is the same belt-and-braces the zip
    reader applies to an archive entry.
    """
    if not NAME_PATTERN.match(name):
        raise SpecError(f"{name!r} is not a valid scorer name")
    root = (directory or SCORER_DIR).resolve()
    target = (root / f"{name}.json").resolve()
    if target.parent != root:
        raise SpecError(f"{name!r} does not resolve inside the scorer directory")
    return target


def save_spec(spec: ScorerSpec, directory: Path | None = None) -> Path:
    root = directory or SCORER_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = spec_path(spec.name, root)
    path.write_text(json.dumps(spec.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


def delete_spec(name: str, directory: Path | None = None) -> bool:
    path = spec_path(name, directory)
    if not path.exists():
        return False
    path.unlink()
    return True


def load_specs(directory: Path | None = None) -> tuple[list[ScorerSpec], list[str]]:
    """Every spec in `directory`, plus one message per file that could
    not be loaded.

    One bad file never hides the good ones: a spec that fails validation
    is reported by name and skipped, exactly as a batch tool reports one
    bad entry and continues. The caller decides whether a given failure
    matters -- `cli.py` refuses only when the BROKEN spec is the one the
    run named.
    """
    root = directory or SCORER_DIR
    specs: list[ScorerSpec] = []
    errors: list[str] = []
    if not root.is_dir():
        return specs, errors
    for path in sorted(root.glob("*.json")):
        try:
            parsed = parse_spec(json.loads(path.read_text(encoding="utf-8")))
        except (SpecError, json.JSONDecodeError, OSError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        # The same proof the console runs on save: this directory is
        # committed and hand-editable, so a spec the console would refuse
        # must not load through `--classifier` either (S-01.1 re-stamp #2 G-1).
        problems = verify_monotone(parsed)
        if problems:
            errors.append(f"{path.name}: relaxes its base classifier: {problems[0]}")
            continue
        specs.append(parsed)
    return specs, errors


def register_custom_classifiers(
    directory: Path | None = None,
) -> tuple[dict[str, ScorerSpec], list[str]]:
    """Add every valid spec in `directory` to `CLASSIFIERS`.

    Returns `({name: spec}, errors)`. A shipped classifier is never
    overwritten -- `parse_spec` refuses that name -- so calling this can
    change what `--classifier my-rule` resolves to but can never change
    what `--classifier regression` does.

    This is an EXPLICIT call, not an import side effect: a run that does
    not reach it behaves exactly as it did before this module existed.
    """
    specs, errors = load_specs(directory)
    registered: dict[str, ScorerSpec] = {}
    for spec in specs:
        CLASSIFIERS[spec.name] = build_classifier(spec)
        registered[spec.name] = spec
    return registered, errors
