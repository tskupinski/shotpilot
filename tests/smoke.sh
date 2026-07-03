#!/bin/sh
# Smoke test of the full vm cycle on a synthetic video — fully ISOLATED in a
# temp directory: it never touches the real project's input/, output/ or
# manifest, so it is safe to run at any time, even mid-project or in parallel
# with renders. Exit 0 = everything works (~1 min).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SMOKE_DIR=$(mktemp -d)
trap 'rm -rf "$SMOKE_DIR"' EXIT
cd "$SMOKE_DIR"
mkdir -p input

vm_run() { PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline "$@"; }
py_run() { PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" "$@"; }
VM=vm_run
PY=py_run

SYN=input/test_synthetic.mp4
TINY=input/tiny.mp4
SEL=output/selects/test_synthetic_smoke.mp4
SEL2=output/selects/test_synthetic_smoke2.mp4
SELX2=output/selects/test_synthetic_smoke_x2.mp4
MONT=output/smoke_montage.mp4
ALT=output/smoke_montage_alt.mp4
FINAL=output/smoke_final.mp4
MUSIC=smoke_music.wav
MUSIC_SHORT=smoke_music_short.wav

fail() { echo "SMOKE FAIL: $1" >&2; exit 1; }

assert_json() {  # assert_json '<expr on d>' <<< json
    $PY -c "import json,sys; d=json.load(sys.stdin); assert ($1)" || fail "$1"
}

$PY "$ROOT/tests/make_test_video.py" "$SYN" >&2

echo "[1/15] scan (+ contact sheet in a single pass)" >&2
$VM scan "$SYN" --force --json 2>/dev/null \
    | assert_json "d['results'][0]['stats']['n_segments'] == 3"
[ -f output/test_synthetic/contact.png ] || fail "scan did not generate contact.png"
[ -f output/test_synthetic/review.png ] || fail "scan did not generate review.png"
# regression: a sub-second accidental clip must scan clean (0 segments), not crash
# (smoothness_score convolve window used to exceed the signal length)
ffmpeg -nostdin -y -v error -f lavfi -i "testsrc2=size=640x360:rate=30:duration=0.4" "$TINY"
$VM scan "$TINY" --json 2>/dev/null \
    | assert_json "d['results'][0]['stats']['n_segments'] == 0"

echo "[2/15] jitter" >&2
$VM jitter "$SYN" --from 9 --to 11 --json 2>/dev/null \
    | assert_json "d['verdict'] == 'jitter'"
$VM jitter "$SYN" --from 14 --to 16 --json 2>/dev/null \
    | assert_json "d['verdict'] == 'smooth-maneuver'"

echo "[3/15] sheet (cache) + frames" >&2
$VM sheet "$SYN" --json 2>sheet.err \
    | assert_json "d['results'][0]['sheet'].endswith('contact.png')"
grep -q "cache" sheet.err || fail "sheet did not use the cache after scan"
$VM frames "$SYN" 1 15 --json 2>/dev/null \
    | assert_json "len(d['frames']) == 2"

echo "[4/15] select (single + --plan resume)" >&2
$VM select "$SYN" 13 18 --label smoke --stars 1 --note "smoke test" --json 2>/dev/null \
    | assert_json "d['range'] == [13.0, 18.0] and d['stars'] == 1"
[ -f "$SEL" ] || fail "select file missing"
# --plan: batch from JSONL; a matching, already-cut select is SKIPPED (= resume
# semantics after an interrupted batch), the missing one is cut
printf '{"file": "%s", "start": 13, "end": 18, "label": "smoke", "stars": 1}\n{"file": "%s", "start": 0.5, "end": 5.5, "label": "smoke2", "stars": 1}\n' "$SYN" "$SYN" > plan.jsonl
$VM select --plan plan.jsonl --json 2>/dev/null \
    | assert_json "d['results'][0].get('skipped') is True and d['results'][1]['range'] == [0.5, 5.5]"
[ -f "$SEL2" ] || fail "select --plan did not cut the missing select"
$VM select --plan plan.jsonl --label x 2>/dev/null && fail "select --plan accepted --label" || true

echo "[5/15] pace (source motion reuse + profile) + speed + guard" >&2
$VM pace "$SEL" --profile --json 2>pace.err \
    | assert_json "d['results'][0]['pace']['total_pct_s'] > 0 and d['results'][0]['profile']['windows'] and 't0_src' in d['results'][0]['profile']['windows'][0]"
grep -q "motion.csv" pace.err || fail "pace did not reuse the source's motion.csv via the manifest"
$VM speed "$SEL" 2 --json 2>/dev/null \
    | assert_json "abs(d['duration'] - 2.5) < 0.3"
$VM speed "$SELX2" 2 2>/dev/null \
    && fail "speed did not refuse on an _x2 variant" || true

echo "[6/15] status" >&2
$VM status --json 2>/dev/null \
    | assert_json "any(s['label'] == 'smoke' and s['speed_variants'] for s in d['selects'])"

echo "[7/15] tag" >&2
$VM tag "$SEL" --scene synthetic --shot panorama --light midday --json 2>/dev/null \
    | assert_json "d['results'][0]['tags'] == {'scene': 'synthetic', 'shot': 'panorama', 'light': 'midday'}"
$VM tag "$SEL" --scene "Bad Tags" 2>/dev/null && fail "tag accepted non-kebab-case" || true
$VM tag "$SEL" --role hook --json 2>/dev/null \
    | assert_json "d['results'][0]['tags']['role'] == 'hook' and d['results'][0]['tags']['scene'] == 'synthetic'"
$VM tag "$SEL" --role bad-role 2>/dev/null && fail "tag accepted a role outside the dictionary" || true
$VM tag "$SEL2" --scene synthetic --shot top-down --light midday --json >/dev/null 2>&1
# reject: permanent exclusion of a select (reject field + status)
$VM tag "$SEL2" --reject --json 2>/dev/null \
    | assert_json "d['results'][0]['reject'] is True"
$VM status --json 2>/dev/null \
    | assert_json "d['totals']['rejected'] >= 1 and any(s['label']=='smoke2' and s.get('reject') for s in d['selects'])"
$VM tag "$SEL2" --reject --unreject 2>/dev/null && fail "tag accepted --reject and --unreject at once" || true
$VM tag "$SEL2" --unreject --json 2>/dev/null \
    | assert_json "d['results'][0]['reject'] is False"

echo "[8/15] sequence + lint + target" >&2
$VM sequence "$SEL2" "$SELX2" --target 30 --json 2>/dev/null \
    | assert_json "abs(d['total_s'] - 7.5) < 0.5 and d['target_s'] == 30 and any(w['type'] == 'adjacent_same_scene' for w in d['warnings']) and any(w['type'] == 'duration_off_target' for w in d['warnings'])"
$VM sequence --target 8 --json 2>/dev/null \
    | assert_json "d['target_s'] == 8 and not any(w['type'] == 'duration_off_target' for w in d['warnings'])"
$VM status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['target_s'] == 8"
$VM sequence no/such/file.mp4 2>/dev/null && fail "sequence accepted an unknown file" || true
# cut decision note (casting/order/lint rationale)
$VM sequence --note "smoke: casting without cuts" --json 2>/dev/null \
    | assert_json "d['notes'] == 'smoke: casting without cuts'"
$VM sequence --append-note "lint OK" --json 2>/dev/null \
    | assert_json "d['notes'] == 'smoke: casting without cuts; lint OK'"
$VM status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['notes'] == 'smoke: casting without cuts; lint OK'"
$VM sequence --note x --append-note y 2>/dev/null && fail "sequence accepted --note and --append-note at once" || true
# reject in the sequence -> rejected_clip lint + outside the casting pool (in 'rejected')
$VM tag "$SEL2" --reject >/dev/null 2>&1
$VM sequence --json 2>/dev/null \
    | assert_json "any(w['type']=='rejected_clip' for w in d['warnings']) and any(r['file'].endswith('smoke2.mp4') for r in d['rejected'])"
$VM tag "$SEL2" --unreject >/dev/null 2>&1

echo "[9/15] montage (draft --xfade 0 + default crossfade + --smooth)" >&2
$VM montage --out "$MONT" --xfade 0 --json 2>/dev/null \
    | assert_json "abs(d['duration'] - 7.5) < 0.5 and d['clips'] == 2 and d['xfade'] == 0 and d['smooth'] is False"
[ -f "$MONT" ] || fail "montage file missing"
$VM montage --out "$MONT" --xfade 0 --smooth --json 2>/dev/null \
    | assert_json "d['smooth'] is False"   # --smooth skipped for stream copy
$VM montage --out "$MONT" --xfade 1.3 2>/dev/null \
    && fail "montage did not refuse with a clip shorter than 2x the transition" || true
$VM montage --out "$MONT" --json 2>/dev/null \
    | assert_json "abs(d['duration'] - 6.5) < 0.5 and d['clips'] == 2 and d['xfade'] == 1.0"
$VM montage --out "$MONT" --smooth --json 2>/dev/null \
    | assert_json "d['smooth'] is True and abs(d['duration'] - 6.5) < 0.5 and d['clips'] == 2"
# named cut: own sequence + render to output/cuts/, main record untouched
$VM sequence --cut smoke-alt "$SELX2" "$SEL2" --json 2>/dev/null \
    | assert_json "d['cut'] == 'smoke-alt' and len(d['sequence']) == 2"
$VM montage --cut smoke-alt --xfade 0 --json 2>/dev/null \
    | assert_json "d['out'] == 'output/cuts/smoke-alt.mp4' and d['clips'] == 2"
[ -f output/cuts/smoke-alt.mp4 ] || fail "montage --cut did not render to output/cuts/"
$VM locate --cut smoke-alt --json 2>/dev/null \
    | assert_json "len(d['timeline']) == 2 and d['render']['state'] == 'fresh'"
$PY -c "from pipeline import manifest; assert manifest.get_cut()['render']['out'] == '$MONT'" \
    || fail "cut render overwrote the main render record"
# --files: render an external version to --out, WITHOUT touching the manifest (render.out stays)
$VM montage --files "$SEL2" "$SELX2" --out "$ALT" --xfade 0 --json 2>/dev/null \
    | assert_json "d['render_state'] == 'external' and abs(d['duration'] - 7.5) < 0.5 and d['clips'] == 2"
[ -f "$ALT" ] || fail "montage --files did not render"
$PY -c "from pipeline import manifest; assert manifest.get_cut()['render']['out'] == '$MONT'" \
    || fail "montage --files overwrote the render record in the manifest"
$VM montage --files "$SEL2" "$SELX2" 2>/dev/null && fail "montage --files without --out did not refuse" || true
# smooth_clip: PER-CLIP motion interpolation into the cache (core of --smooth, memory-safe)
ffmpeg -nostdin -y -v error -i "$SEL2" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p -an smoke_30.mp4
$PY -c "
from pathlib import Path
from pipeline import montage as m
from pipeline.probe import probe
d = m.smooth_clip(Path('smoke_30.mp4'), '60/1')           # 30 -> 60 fps (off-target)
assert 'smooth-cache' in str(d) and d.exists(), d
assert probe(str(d)).fps > 50, probe(str(d)).fps          # actually at the target
assert m.smooth_clip(Path('smoke_30.mp4'), '60/1') == d   # reuse from cache
" || fail "smooth_clip did not smooth to target / did not cache"
# vm smooth: explicit cache warm-up (reusing the entry from the previous step = instant)
$VM smooth smoke_30.mp4 --fps 60/1 --json 2>/dev/null \
    | assert_json "len(d['smoothed']) == 1 and d['on_target'] == 0 and d['target_fps'] == '60/1'"
$VM status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['render']['state'] == 'fresh'"

echo "[10/15] locate (time->clip, reverse lookup, timeline)" >&2
$VM locate --json 2>/dev/null \
    | assert_json "len(d['timeline']) == 2 and abs(d['film_s'] - 6.5) < 0.6 and d['render']['state'] == 'fresh'"
$VM locate 0:03 smoke2 --json 2>/dev/null \
    | assert_json "d['results'][0]['mode'] == 'time' and d['results'][0]['clip']['index'] == 0 and d['results'][1]['matches'] == [0]"
$VM locate 99:00 --json 2>/dev/null \
    | assert_json "d['results'][0]['clip'] is None"
$VM locate 0:02 --files "$SEL2" "$SELX2" --json 2>/dev/null \
    | assert_json "d['render']['state'] == 'external' and d['results'][0]['clip']['index'] == 0"
$VM locate --files "$SEL2" "$SELX2" --json 2>/dev/null \
    | assert_json "len(d['timeline']) == 2 and d['timeline'][1]['source'].endswith('test_synthetic.mp4')"
$VM locate 0:03 --files "$MONT" --json 2>/dev/null \
    | assert_json "d['results'][0]['clip']['label'] == 'smoke_montage'"
$VM locate 0:01 --files no/such/file.mp4 2>/dev/null && fail "locate --files accepted a nonexistent file" || true

echo "[11/15] music (probe + mux + loop + staleness)" >&2
ffmpeg -nostdin -y -v error -f lavfi -i "sine=frequency=440:duration=12" "$MUSIC"
$VM music --probe "$MUSIC" --json 2>/dev/null \
    | assert_json "abs(d['results'][0]['duration_s'] - 12) < 0.2 and d['results'][0]['energy'] and d['results'][0]['integrated_lufs'] is not None"
$VM music "$MUSIC" --out "$FINAL" --json 2>/dev/null \
    | assert_json "abs(d['video_s'] - 6.5) < 0.5 and d['audio_s'] <= d['video_s'] + 0.1 and not d['looped']"
[ -f "$FINAL" ] || fail "final file missing"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$FINAL" \
    | grep -q audio || fail "final has no audio stream"
DUR_FIN=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$FINAL")
$PY -c "assert abs($DUR_FIN - 6.5) < 0.5, $DUR_FIN" || fail "final duration deviates from the montage"
$VM status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['music']['applied']['state'] == 'fresh'"
ffmpeg -nostdin -y -v error -f lavfi -i "sine=frequency=330:duration=3" "$MUSIC_SHORT"
$VM music "$MUSIC_SHORT" --loop --out "$FINAL" --json 2>/dev/null \
    | assert_json "d['looped'] and d['gap_s'] < 0.6 and abs(d['audio_s'] - d['video_s']) < 0.6"

echo "[12/15] trim (re-cut from source + variant refresh + staleness + cut guard)" >&2
# the select also plays in a second cut -> trim must warn that it changes all cuts
$VM sequence --cut smoke-b "$SEL" >/dev/null 2>&1
$VM trim "$SEL" 14 17 --note "smoke trim" --json 2>trim.err \
    | assert_json "d['range'] == [14.0, 17.0] and d['range_history'] == [[13.0, 18.0]] and d['variants_refreshed']"
grep -q "smoke-b" trim.err || fail "trim did not warn about a select shared between cuts"
DUR_X2=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SELX2")
$PY -c "assert abs($DUR_X2 - 1.5) < 0.3, $DUR_X2" || fail "x2 variant not refreshed after trim"
$VM status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['render']['state'] == 'stale' and d['cuts']['main']['music']['applied']['state'] == 'stale'"
# remove also clears sequence entries from EVERY cut (everything here is isolated
# in the smoke dir, so nothing real needs restoring)
$PY -c "from pipeline import manifest; manifest.remove('$SEL'); manifest.remove('$SEL2')"
$PY -c "from pipeline import manifest; assert manifest.get_cut()['sequence'] == []" \
    || fail "remove did not clear the montage sequence"

echo "[13/15] config: custom input folder (isolated tmp)" >&2
TMP=$(mktemp -d)
mkdir -p "$TMP/output" "$TMP/sdcard"
touch "$TMP/sdcard/card.mp4"
run_tmp() { ( cd "$TMP" && PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline "$@" 2>/dev/null ); }
run_tmp config --input-dir sdcard --json \
    | assert_json "d['input_dir'] == 'sdcard' and d['input_dir_source'] == 'config.json'"
run_tmp status --json \
    | assert_json "d['input_dir'] == 'sdcard' and d['inputs'][0]['file'].endswith('card.mp4')"
run_tmp config --input-dir no-such-dir --json && fail "config accepted a nonexistent directory" || true
( cd "$TMP" && VM_INPUT_DIR=elsewhere PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline config --json 2>/dev/null ) \
    | assert_json "d['input_dir'] == 'elsewhere' and d['input_dir_source'] == 'env VM_INPUT_DIR'"
run_tmp config --reset --json \
    | assert_json "d['input_dir'] == 'input' and d['input_dir_source'] == 'default'"
[ ! -f "$TMP/config.json" ] || fail "reset did not remove config.json"
# .env loader: variables from the file land in env, but the real env wins
printf '# comment\nVM_INPUT_DIR=sdcard\n' > "$TMP/.env"
run_tmp config --json \
    | assert_json "d['input_dir'] == 'sdcard' and d['input_dir_source'] == 'env VM_INPUT_DIR'"
( cd "$TMP" && VM_INPUT_DIR=elsewhere PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline config --json 2>/dev/null ) \
    | assert_json "d['input_dir'] == 'elsewhere'" \
    || fail "real env did not win over .env"
rm -rf "$TMP"

echo "[14/15] archive + restore (isolated tmp)" >&2
TMP=$(mktemp -d)
mkdir -p "$TMP/output" "$TMP/input"
echo '{"selects": []}' > "$TMP/output/project.json"
touch "$TMP/input/fake.mp4"
( cd "$TMP" && PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline archive smoke --with-input --json 2>/dev/null ) \
    | assert_json "d['archive'].startswith('archive/') and d['inputs_moved'] == ['fake.mp4']"
[ ! -f "$TMP/input/fake.mp4" ] || fail "input was not moved"
[ -z "$(ls -A "$TMP/output")" ] || fail "output is not empty after archiving"
ARCH=$(ls "$TMP/archive")
( cd "$TMP" && PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline restore "$ARCH" --json 2>/dev/null ) \
    | assert_json "d['inputs_moved'] == ['fake.mp4'] and d['selects'] == 0"
[ -f "$TMP/input/fake.mp4" ] || fail "restore did not bring back the input"
[ -f "$TMP/output/project.json" ] || fail "restore did not bring back the output"
[ ! -d "$TMP/archive/$ARCH" ] || fail "empty archive was not cleaned up"
rm -rf "$TMP"

echo "[15/15] publish (thumbnail + description, isolated tmp)" >&2
TMP=$(mktemp -d)
mkdir -p "$TMP/output"
ffmpeg -nostdin -y -v error -f lavfi -i "testsrc2=size=1920x1080:rate=24:duration=2" "$TMP/src.mp4"
printf 'BOILER\nLINE2\n' > "$TMP/tpl.txt"
printf 'Specific part.\n' > "$TMP/spec.txt"
run_pub() { ( cd "$TMP" && VM_PUBLISH_TEMPLATE=tpl.txt PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline "$@" 2>/dev/null ); }
run_pub publish --frame src.mp4 --at 1 --text "Smoke Lake" --subtitle "cinematic" \
        --title "Smoke Film" --description-file spec.txt --json \
    | assert_json "d['thumbnail']['width'] == 1280 and d['thumbnail']['height'] == 720 and d['thumbnail']['text'] == 'SMOKE LAKE' and d['title'] == 'Smoke Film'"
$PY -c "from PIL import Image; im = Image.open('$TMP/output/publish/thumbnail.jpg'); assert im.size == (1280, 720), im.size" \
    || fail "thumbnail does not have 1280x720 dimensions"
$PY -c "t = open('$TMP/output/publish/description.txt').read(); assert t.index('Specific') < t.index('BOILER'), t" \
    || fail "specific part of the description is not before the boilerplate"
run_pub status --json \
    | assert_json "d['publish']['title'] == 'Smoke Film' and d['publish']['thumbnail']['at_s'] == 1.0"
# custom --out = working candidate, without overwriting the thumbnail entry in the manifest
run_pub publish --frame src.mp4 --at 0.5 --text "Cand" --out output/publish/cand-1.jpg --json \
    | assert_json "d['thumbnail']['out'].endswith('cand-1.jpg')"
run_pub status --json \
    | assert_json "d['publish']['thumbnail']['at_s'] == 1.0"
# --title alone (no description) saves the title; --description-file without --title refuses
run_pub publish --title "Solo" --json \
    | assert_json "d['title'] == 'Solo' and 'description_file' not in d"
run_pub status --json \
    | assert_json "d['publish']['title'] == 'Solo' and d['publish']['description_file']"
run_pub publish --description-file spec.txt && fail "publish accepted a description without a title" || true
run_pub publish --frame src.mp4 --at 99 --text "X" && fail "publish accepted a frame time beyond the video" || true
( cd "$TMP" && PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline publish --title "T" --description-file spec.txt 2>/dev/null ) \
    && fail "publish assembled a description without a template" || true
rm -rf "$TMP"
echo "SMOKE OK" >&2
