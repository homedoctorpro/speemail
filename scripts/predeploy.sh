#!/usr/bin/env bash
# Pre-deploy sanity checks. Run before pushing or deploying.
set -e

echo "=== Speemail pre-deploy checks ==="

echo ""
echo "--- Syntax check (all Python files) ---"
python -m compileall speemail/ -q
echo "OK"

echo ""
echo "--- Ruff lint ---"
python -m ruff check speemail/ || { echo "FAIL: lint errors"; exit 1; }
echo "OK"

echo ""
echo "--- Import check (app factory) ---"
python -c "from speemail.api.app import create_app; app = create_app(); print('App factory: OK')"

echo ""
echo "--- Unit / smoke tests ---"
python -m pytest tests/ -v --tb=short

echo ""
echo "=== All checks passed ==="
