#!/usr/bin/env bash
# Launcher do tclcast — usa o venv do projeto.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Criando venv…"
  python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install --quiet --upgrade pip
  "$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
fi
exec "$PY" -m tclcast "$@"
