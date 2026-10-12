#!/usr/bin/env bash
# Build lambda/ai-analyst/build/ (handler + Linux wheels) for Terraform's archive_file.
set -euo pipefail
cd "$(dirname "$0")/../lambda/ai-analyst"
rm -rf build && mkdir build
python3 -m pip install --quiet --target build --platform manylinux2014_x86_64 \
  --implementation cp --python-version 3.13 --only-binary=:all: -r requirements.txt
cp handler.py build/
find build -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "lambda package ready: $(du -sh build | cut -f1)"
