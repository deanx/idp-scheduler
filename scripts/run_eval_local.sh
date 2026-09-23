#!/usr/bin/env bash
# Local convenience wrapper around `run_eval` (ADR-0004 A8, 2026-09-22:
# "Local ergonomics are served the way A8 blessed -- a wrapper outside
# `src/` that reads the IDP_TEST_* family and composes the command
# line").
#
# This script composes a command line from `.env` values; it does NOT
# reintroduce env-as-config. Nothing under `src/` reads IDP_ORG_ID,
# IDP_ACTION_ID or GOLDEN_DATASET_NAME -- that is a static AST pin
# (tests/orchestration/test_static_orchestration_checks.py,
# test_no_production_code_reads_the_retired_env_var_names) and this
# wrapper must not be the thing that breaks it. It only ever appends
# `--org/--action/--version/--dataset/--run` to argv, the same as a
# human typing them.
#
# `--run` is not a stable value in `.env` (a run name identifies ONE
# invocation, not a fixture) -- generated here as a timestamp so two
# local runs never collide in Langfuse's run list.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f .env ]; then
  echo "run_eval_local: .env not found -- copy .env.example to .env and fill it in" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

missing=()
[ -z "${IDP_ORG_ID:-}" ] && missing+=("IDP_ORG_ID")
[ -z "${IDP_ACTION_ID:-}" ] && missing+=("IDP_ACTION_ID")
[ -z "${IDP_TEST_ACTION_VERSION:-}" ] && missing+=("IDP_TEST_ACTION_VERSION")
[ -z "${GOLDEN_DATASET_NAME:-}" ] && missing+=("GOLDEN_DATASET_NAME")

if [ "${#missing[@]}" -gt 0 ]; then
  echo "run_eval_local: missing required .env value(s): ${missing[*]}" >&2
  exit 1
fi

run_name="local-$(date -u +%Y%m%dT%H%M%SZ)"

echo "run_eval_local: --org ${IDP_ORG_ID} --action ${IDP_ACTION_ID} --version ${IDP_TEST_ACTION_VERSION} --dataset ${GOLDEN_DATASET_NAME} --run ${run_name}" >&2

exec .venv/bin/python -m idp_regression.orchestration.cli \
  --org "${IDP_ORG_ID}" \
  --action "${IDP_ACTION_ID}" \
  --version "${IDP_TEST_ACTION_VERSION}" \
  --dataset "${GOLDEN_DATASET_NAME}" \
  --run "${run_name}" \
  "$@"
