#!/usr/bin/env bash
set -euo pipefail
CAPTION_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v uv >/dev/null 2>&1; then
  uv venv "$CAPTION_DIR/.venv"
  uv pip install --python "$CAPTION_DIR/.venv/bin/python" -r "$CAPTION_DIR/requirements.txt"
else
  python3 -m venv "$CAPTION_DIR/.venv"
  "$CAPTION_DIR/.venv/bin/python" -m pip install -r "$CAPTION_DIR/requirements.txt"
fi
printf '\nInstalled. Run: bash "%s/start.sh" --demo\n' "$CAPTION_DIR"
