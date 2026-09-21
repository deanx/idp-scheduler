#!/usr/bin/env bash
# T-01.4.10 (DEBT-45) -- wire leg 2 (gitleaks pre-commit hook) into this
# clone. Run once per clone:
#
#   ./scripts/install-git-hooks.sh
#
# core.hooksPath is a per-clone (not versioned) git config value, so this
# cannot be "committed" -- every contributor runs this once.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
git -C "$repo_root" config core.hooksPath .githooks
echo "core.hooksPath -> .githooks (gitleaks pre-commit gate wired)"
