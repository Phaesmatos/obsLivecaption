#!/usr/bin/env bash
set -euo pipefail
CAPTION_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CAPTION_PYTHON=python3
if [[ -x "$CAPTION_DIR/.venv/bin/python" ]]; then
  CAPTION_PYTHON="$CAPTION_DIR/.venv/bin/python"
fi
CAPTION_NEEDS_KEY=true
CAPTION_DEEPL=false
for CAPTION_ARG in "$@"; do
  case "$CAPTION_ARG" in
    --deepl|--depel) CAPTION_DEEPL=true ;;
    --demo|--list-devices|--check-audio|--check-audio=*|--help|-h) CAPTION_NEEDS_KEY=false ;;
  esac
done
if $CAPTION_NEEDS_KEY && [[ -z "${GLADIA_API_KEY:-}" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Run this launcher in a terminal, or set GLADIA_API_KEY in its environment." >&2
    exit 1
  fi
  read -r -s -p "Gladia API key (hidden, kept only for this process): " GLADIA_API_KEY
  printf '\n'
  if [[ -z "$GLADIA_API_KEY" ]]; then
    echo "No API key entered." >&2
    exit 1
  fi
  export GLADIA_API_KEY
fi
if $CAPTION_NEEDS_KEY && $CAPTION_DEEPL && [[ -z "${DEEPL_API_KEY:-}" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Run in a terminal, or set DEEPL_API_KEY in the environment." >&2
    exit 1
  fi
  read -r -s -p "DeepL API key (hidden, kept only for this process): " DEEPL_API_KEY
  printf '\n'
  if [[ -z "$DEEPL_API_KEY" ]]; then
    echo "No DeepL API key entered." >&2
    exit 1
  fi
  export DEEPL_API_KEY
fi
exec "$CAPTION_PYTHON" "$CAPTION_DIR/gladia_captions.py" "$@"
