#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)-$(uname -m)" != Linux-x86_64 ]]; then
  echo "Build this release on Linux x86-64." >&2
  exit 1
fi

uv run --locked python -c 'from strathive.adapters.sqlite import SQLiteStore; SQLiteStore.check_sqlite()'
uv run --locked pyinstaller --noconfirm --onedir --name strathive \
  --specpath build --workpath build/freeze --distpath build/release \
  --hidden-import sqlalchemy.dialects.sqlite.pysqlite \
  --add-data "$PWD/src/strathive/adapters/migrations/env.py:strathive/adapters/migrations" \
  --add-data "$PWD/src/strathive/adapters/migrations/versions/*.py:strathive/adapters/migrations/versions" \
  --add-data "$PWD/src/strathive/resources:strathive/resources" \
  src/strathive/__main__.py

bundle=build/release/strathive
cp README.md "$bundle/"
cp -R skills "$bundle/"
mkdir -p dist
tar -C build/release -czf dist/strathive-linux-x86_64.tar.gz strathive
