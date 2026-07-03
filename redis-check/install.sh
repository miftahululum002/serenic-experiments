#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .env ]; then
  echo "Error: file .env tidak ditemukan. Copy dari .env.example dan isi konfigurasinya."
  exit 1
fi

set -a; source .env; set +a

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "Error: GITHUB_TOKEN tidak terisi di .env"
  exit 1
fi

pip install -r requirements.txt
pip install "serenic_mlkit @ git+https://${GITHUB_TOKEN}@github.com/Serenic-AI/serenic_mlkit.git"

echo "Done."
