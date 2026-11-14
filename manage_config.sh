#!/usr/bin/env bash
# Keep all editing logic in Python so Linux and Windows use the same schema.
set -e

UAP_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UAP_PYTHON_BIN=""
for UAP_CANDIDATE in "$UAP_SCRIPT_DIR/venv/bin/python" "$UAP_SCRIPT_DIR/.venv/bin/python" python3 python; do
    if command -v "$UAP_CANDIDATE" >/dev/null 2>&1 &&
       "$UAP_CANDIDATE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        UAP_PYTHON_BIN="$UAP_CANDIDATE"
        break
    fi
done

if [[ -z "$UAP_PYTHON_BIN" ]]; then
    echo "ต้องใช้ Python 3.10 ขึ้นไปเพื่อเปิดตัวจัดการ config" >&2
    exit 1
fi

exec "$UAP_PYTHON_BIN" -X utf8 "$UAP_SCRIPT_DIR/config_editor.py" \
    --config "$UAP_SCRIPT_DIR/config.json" "$@"
