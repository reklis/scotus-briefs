#!/bin/sh
set -eu

python_bin=${PYTHON_BIN:-.venv/bin/python}
"$python_bin" -m pytest -q \
  tests/test_source_http_contracts.py \
  tests/test_scotus_discovery.py \
  tests/test_scotus_documents.py \
  tests/test_scotus_extraction.py \
  tests/test_scotus_correlation.py \
  tests/test_scotus_briefs.py \
  tests/test_scotus_public.py \
  tests/test_scotus_e2e.py

kubectl kustomize deploy/k8s/base >/dev/null
sh -n scripts/backup-postgres.sh scripts/restore-postgres.sh scripts/verify-scotus-recovery.sh
printf '%s\n' 'SCOTUS fault-injection and static deployment checks passed'
