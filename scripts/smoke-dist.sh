#!/usr/bin/env bash
# Prove a built distribution actually runs before it can be published.
#
# Installs the given wheel (or sdist) into a throwaway venv *outside* the repo and
# starts a real app with no TUBULIN_* env set, so nothing can be satisfied by the
# source checkout: the interpreter, the Python package and the frontend all have to
# come from inside the distribution. Checks the frontend too, not just /health —
# an install that serves 404 for every page still answers /health perfectly well.
#
# Usage: scripts/smoke-dist.sh <path-to-.whl-or-.tar.gz> [port]
set -euo pipefail

DIST="${1:?usage: smoke-dist.sh <dist-file> [port]}"
PORT="${2:-5599}"
[ -f "$DIST" ] || { echo "no such distribution: $DIST"; exit 2; }
case "$PORT" in ''|*[!0-9]*) echo "port must be numeric, got: $PORT"; exit 2 ;; esac
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'kill "${APP_PID:-}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

echo "==> installing $(basename "$DIST") into a clean venv"
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --quiet --upgrade pip
# An sdist must be built from source; a wheel installs as-is. Either way the
# dependencies come from wheels — we are testing this package, not polars.
case "$DIST" in
  *.tar.gz) "$WORK/venv/bin/pip" install --quiet --no-binary tubulin "$DIST" ;;
  *)        "$WORK/venv/bin/pip" install --quiet "$DIST" ;;
esac

echo "==> starting an app from outside the repo"
mkdir -p "$WORK/app"
cp "$ROOT/examples/widgets/ui.py" "$WORK/app/"
cd "$WORK/app"
env -u TUBULIN_ROOT -u TUBULIN_PYTHON -u TUBULIN_FRONTEND \
  "$WORK/venv/bin/tubulin" run ui.py --port "$PORT" --no-reload > "$WORK/app.log" 2>&1 &
APP_PID=$!

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done

fail() { echo "FAIL: $1"; echo "--- app log ---"; cat "$WORK/app.log"; exit 1; }

curl -sf "http://127.0.0.1:$PORT/health" | grep -q '"status":"ok"' || fail "/health did not report ok"
echo "    /health ok"

# The frontend must come from inside the distribution, not a source checkout.
INDEX="$(curl -sf "http://127.0.0.1:$PORT/" || true)"
grep -q '<div id="root">' <<<"$INDEX" || fail "index.html did not come from the package"
echo "    index.html ok"

# ...and its assets must really be there. A bare index with 404ing assets is the
# exact failure an sdist without a staged frontend used to produce.
for ext in js css; do
  path="$(grep -oE "/assets/[A-Za-z0-9._-]+\.$ext" <<<"$INDEX" | head -1)"
  [ -n "$path" ] || fail "index.html references no .$ext asset"
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$path")"
  [ "$code" = "200" ] || fail "asset $path returned HTTP $code"
  echo "    $path ok"
done

# Spelled out rather than `grep && fail`: under `set -e` the exit status of an
# unmatched grep in an AND list is easy to get subtly wrong.
if grep -q 'frontend build not found' "$WORK/app.log"; then
  fail "runtime warned about a missing frontend"
fi

echo "==> $(basename "$DIST") is runnable"
