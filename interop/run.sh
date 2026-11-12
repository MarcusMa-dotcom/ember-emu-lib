#!/bin/bash
# Runs emberemu against emberplus-connection in both directions and compares the encodings.
#
#   npm install --ignore-scripts        (once, in this folder)
#   ./run.sh                            (uses python3, or the interpreter named in PYTHON)
#
# Ports 9481 to 9483 on this machine are used for the duration of the run.
cd "$(dirname "$0")" || exit 1
PYTHON="${PYTHON:-python3}"
if [ ! -d node_modules/emberplus-connection ]; then
  echo "Run 'npm install --ignore-scripts' in this folder first." >&2
  exit 1
fi
failed=0

# Ends a background process that is still running after the given number of seconds.
limit() { ( sleep "$1"; kill "$2" 2>/dev/null ) & }

echo "== 1. Their consumer reads the provider of emberemu"
"$PYTHON" -u py_provider.py 9481 14 > provider.log 2>&1 &
provider=$!
sleep 1
node node_consumer.js 9481 &
consumer=$!
limit 40 $consumer
wait $consumer || failed=1
wait $provider
grep -E "PY (ERROR|final)" provider.log

echo "== 2. The consumer of emberemu reads their provider"
node node_provider.js 9483 20 > node-provider.log 2>&1 &
provider=$!
sleep 1.5
"$PYTHON" -u py_consumer.py 9483 &
consumer=$!
limit 40 $consumer
wait $consumer || failed=1
kill $provider 2>/dev/null

echo "== 3. Both encode the same values"
"$PYTHON" py_codec_make.py mine.json && node node_codec.js mine.json theirs.json && "$PYTHON" py_codec_check.py theirs.json || failed=1

if [ $failed -eq 0 ]; then echo "All three parts passed."; else echo "A part failed, see above."; fi
exit $failed
