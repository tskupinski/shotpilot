#!/bin/sh
# Smoke test of the full shot cycle on a synthetic video — fully ISOLATED in a
# temp directory: it never touches the real project's input/, output/ or
# manifest, so it is safe to run at any time, even mid-project or in parallel
# with renders. Exit 0 = everything works (~1 min).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SMOKE_DIR=$(mktemp -d)
UI_PID=""
# `|| true`: with UI_PID empty (the normal end state) a bare `kill` fails,
# and under set -e that turned a successful run into exit != 0
trap 'kill $UI_PID 2>/dev/null || true; rm -rf "$SMOKE_DIR"' EXIT
cd "$SMOKE_DIR"
mkdir -p input

shot_run() { PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline "$@"; }
py_run() { PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" "$@"; }
SHOT=shot_run
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

echo "[1/18] scan (+ contact sheet in a single pass)" >&2
$SHOT scan "$SYN" --force --json 2>/dev/null \
    | assert_json "d['results'][0]['stats']['n_segments'] == 3"
[ -f output/test_synthetic/contact.png ] || fail "scan did not generate contact.png"
[ -f output/test_synthetic/review.png ] || fail "scan did not generate review.png"
# regression: a sub-second accidental clip must scan clean (0 segments), not crash
# (smoothness_score convolve window used to exceed the signal length)
ffmpeg -nostdin -y -v error -f lavfi -i "testsrc2=size=640x360:rate=30:duration=0.4" "$TINY"
$SHOT scan "$TINY" --json 2>/dev/null \
    | assert_json "d['results'][0]['stats']['n_segments'] == 0"

echo "[2/18] jitter" >&2
$SHOT jitter "$SYN" --from 9 --to 11 --json 2>/dev/null \
    | assert_json "d['verdict'] == 'jitter'"
$SHOT jitter "$SYN" --from 14 --to 16 --json 2>/dev/null \
    | assert_json "d['verdict'] == 'smooth-maneuver'"

echo "[3/18] sheet (cache) + frames" >&2
$SHOT sheet "$SYN" --json 2>sheet.err \
    | assert_json "d['results'][0]['sheet'].endswith('contact.png')"
grep -q "cache" sheet.err || fail "sheet did not use the cache after scan"
$SHOT frames "$SYN" 1 15 --json 2>/dev/null \
    | assert_json "len(d['frames']) == 2"

echo "[4/18] select (single + --plan resume)" >&2
$SHOT select "$SYN" 13 18 --label smoke --stars 1 --note "smoke test" --json 2>/dev/null \
    | assert_json "d['range'] == [13.0, 18.0] and d['stars'] == 1"
[ -f "$SEL" ] || fail "select file missing"
# --plan: batch from JSONL; a matching, already-cut select is SKIPPED (= resume
# semantics after an interrupted batch), the missing one is cut
printf '{"file": "%s", "start": 13, "end": 18, "label": "smoke", "stars": 1}\n{"file": "%s", "start": 0.5, "end": 5.5, "label": "smoke2", "stars": 1}\n' "$SYN" "$SYN" > plan.jsonl
$SHOT select --plan plan.jsonl --json 2>/dev/null \
    | assert_json "d['results'][0].get('skipped') is True and d['results'][1]['range'] == [0.5, 5.5]"
[ -f "$SEL2" ] || fail "select --plan did not cut the missing select"
$SHOT select --plan plan.jsonl --label x 2>/dev/null && fail "select --plan accepted --label" || true

echo "[5/18] pace (source motion reuse + profile) + speed + guard" >&2
$SHOT pace "$SEL" --profile --json 2>pace.err \
    | assert_json "d['results'][0]['pace']['total_pct_s'] > 0 and d['results'][0]['profile']['windows'] and 't0_src' in d['results'][0]['profile']['windows'][0]"
grep -q "motion.csv" pace.err || fail "pace did not reuse the source's motion.csv via the manifest"
$SHOT speed "$SEL" 2 --json 2>/dev/null \
    | assert_json "abs(d['duration'] - 2.5) < 0.3"
$SHOT speed "$SELX2" 2 2>/dev/null \
    && fail "speed did not refuse on an _x2 variant" || true

echo "[6/18] status" >&2
$SHOT status --json 2>/dev/null \
    | assert_json "any(s['label'] == 'smoke' and s['speed_variants'] for s in d['selects'])"

echo "[7/18] tag" >&2
$SHOT tag "$SEL" --scene synthetic --shot panorama --light midday --json 2>/dev/null \
    | assert_json "d['results'][0]['tags'] == {'scene': 'synthetic', 'shot': 'panorama', 'light': 'midday'}"
$SHOT tag "$SEL" --scene "Bad Tags" 2>/dev/null && fail "tag accepted non-kebab-case" || true
$SHOT tag "$SEL" --role hook --json 2>/dev/null \
    | assert_json "d['results'][0]['tags']['role'] == 'hook' and d['results'][0]['tags']['scene'] == 'synthetic'"
$SHOT tag "$SEL" --role bad-role 2>/dev/null && fail "tag accepted a role outside the dictionary" || true
$SHOT tag "$SEL2" --scene synthetic --shot top-down --light midday --json >/dev/null 2>&1
# reject: permanent exclusion of a select (reject field + status)
$SHOT tag "$SEL2" --reject --json 2>/dev/null \
    | assert_json "d['results'][0]['reject'] is True"
$SHOT status --json 2>/dev/null \
    | assert_json "d['totals']['rejected'] >= 1 and any(s['label']=='smoke2' and s.get('reject') for s in d['selects'])"
$SHOT tag "$SEL2" --reject --unreject 2>/dev/null && fail "tag accepted --reject and --unreject at once" || true
$SHOT tag "$SEL2" --unreject --json 2>/dev/null \
    | assert_json "d['results'][0]['reject'] is False"

echo "[8/18] sequence + lint + target" >&2
$SHOT sequence "$SEL2" "$SELX2" --target 30 --json 2>/dev/null \
    | assert_json "abs(d['total_s'] - 7.5) < 0.5 and d['target_s'] == 30 and any(w['type'] == 'adjacent_same_scene' for w in d['warnings']) and any(w['type'] == 'duration_off_target' for w in d['warnings'])"
$SHOT sequence --target 8 --json 2>/dev/null \
    | assert_json "d['target_s'] == 8 and not any(w['type'] == 'duration_off_target' for w in d['warnings'])"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['target_s'] == 8"
$SHOT sequence no/such/file.mp4 2>/dev/null && fail "sequence accepted an unknown file" || true
# cut decision note (casting/order/lint rationale)
$SHOT sequence --note "smoke: casting without cuts" --json 2>/dev/null \
    | assert_json "d['notes'] == 'smoke: casting without cuts'"
$SHOT sequence --append-note "lint OK" --json 2>/dev/null \
    | assert_json "d['notes'] == 'smoke: casting without cuts; lint OK'"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['notes'] == 'smoke: casting without cuts; lint OK'"
$SHOT sequence --note x --append-note y 2>/dev/null && fail "sequence accepted --note and --append-note at once" || true
# reject in the sequence -> rejected_clip lint + outside the casting pool (in 'rejected')
$SHOT tag "$SEL2" --reject >/dev/null 2>&1
$SHOT sequence --json 2>/dev/null \
    | assert_json "any(w['type']=='rejected_clip' for w in d['warnings']) and any(r['file'].endswith('smoke2.mp4') for r in d['rejected'])"
$SHOT tag "$SEL2" --unreject >/dev/null 2>&1

echo "[9/18] montage (draft --xfade 0 + crossfade + --smooth + skip/--force + --draft)" >&2
$SHOT montage --out "$MONT" --xfade 0 --json 2>/dev/null \
    | assert_json "abs(d['duration'] - 7.5) < 0.5 and d['clips'] == 2 and d['xfade'] == 0 and d['smooth'] is False"
[ -f "$MONT" ] || fail "montage file missing"
# identical fresh render -> skipped without touching ffprobe/ffmpeg
$SHOT montage --out "$MONT" --xfade 0 --json 2>/dev/null \
    | assert_json "d.get('skipped') is True and d['xfade'] == 0"
$SHOT montage --out "$MONT" --xfade 0 --smooth --force --json 2>/dev/null \
    | assert_json "d['smooth'] is False and 'skipped' not in d"   # --smooth skipped for stream copy
$SHOT montage --out "$MONT" --xfade 1.3 2>/dev/null \
    && fail "montage did not refuse with a clip shorter than 2x the transition" || true
$SHOT montage --out "$MONT" --json 2>/dev/null \
    | assert_json "abs(d['duration'] - 6.5) < 0.5 and d['clips'] == 2 and d['xfade'] == 1.0"
$SHOT montage --out "$MONT" --smooth --json 2>/dev/null \
    | assert_json "d['smooth'] is True and abs(d['duration'] - 6.5) < 0.5 and d['clips'] == 2"
# smooth render fresh -> skip; --force re-renders
$SHOT montage --out "$MONT" --smooth --json 2>/dev/null \
    | assert_json "d.get('skipped') is True and d['smooth'] is True"
$SHOT montage --out "$MONT" --smooth --force --json 2>/dev/null \
    | assert_json "'skipped' not in d and d['smooth'] is True"
# --draft: preview encode, marked in the record/status; never satisfies a final request
$SHOT montage --out "$MONT" --draft --json 2>/dev/null \
    | assert_json "d['draft'] is True and abs(d['duration'] - 6.5) < 0.5"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['render'].get('draft') is True"
$SHOT montage --out "$MONT" --json 2>/dev/null \
    | assert_json "d['draft'] is False and 'skipped' not in d"
# named cut: own sequence + render to output/cuts/, main record untouched
$SHOT sequence --cut smoke-alt "$SELX2" "$SEL2" --json 2>/dev/null \
    | assert_json "d['cut'] == 'smoke-alt' and len(d['sequence']) == 2"
$SHOT montage --cut smoke-alt --xfade 0 --json 2>/dev/null \
    | assert_json "d['out'] == 'output/cuts/smoke-alt.mp4' and d['clips'] == 2"
[ -f output/cuts/smoke-alt.mp4 ] || fail "montage --cut did not render to output/cuts/"
$SHOT locate --cut smoke-alt --json 2>/dev/null \
    | assert_json "len(d['timeline']) == 2 and d['render']['state'] == 'fresh'"
$PY -c "from pipeline import manifest; assert manifest.get_cut()['render']['out'] == '$MONT'" \
    || fail "cut render overwrote the main render record"
# --files: render an external version to --out, WITHOUT touching the manifest (render.out stays)
$SHOT montage --files "$SEL2" "$SELX2" --out "$ALT" --xfade 0 --json 2>/dev/null \
    | assert_json "d['render_state'] == 'external' and abs(d['duration'] - 7.5) < 0.5 and d['clips'] == 2"
[ -f "$ALT" ] || fail "montage --files did not render"
$PY -c "from pipeline import manifest; assert manifest.get_cut()['render']['out'] == '$MONT'" \
    || fail "montage --files overwrote the render record in the manifest"
$SHOT montage --files "$SEL2" "$SELX2" 2>/dev/null && fail "montage --files without --out did not refuse" || true
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
# shot smooth: explicit cache warm-up (reusing the entry from the previous step = instant)
$SHOT smooth smoke_30.mp4 --fps 60/1 --json 2>/dev/null \
    | assert_json "len(d['smoothed']) == 1 and d['on_target'] == 0 and d['target_fps'] == '60/1'"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['render']['state'] == 'fresh'"

echo "[10/18] locate (time->clip, reverse lookup, timeline)" >&2
$SHOT locate --json 2>/dev/null \
    | assert_json "len(d['timeline']) == 2 and abs(d['film_s'] - 6.5) < 0.6 and d['render']['state'] == 'fresh'"
$SHOT locate 0:03 smoke2 --json 2>/dev/null \
    | assert_json "d['results'][0]['mode'] == 'time' and d['results'][0]['clip']['index'] == 0 and d['results'][1]['matches'] == [0]"
$SHOT locate 99:00 --json 2>/dev/null \
    | assert_json "d['results'][0]['clip'] is None"
$SHOT locate 0:02 --files "$SEL2" "$SELX2" --json 2>/dev/null \
    | assert_json "d['render']['state'] == 'external' and d['results'][0]['clip']['index'] == 0"
$SHOT locate --files "$SEL2" "$SELX2" --json 2>/dev/null \
    | assert_json "len(d['timeline']) == 2 and d['timeline'][1]['source'].endswith('test_synthetic.mp4')"
$SHOT locate 0:03 --files "$MONT" --json 2>/dev/null \
    | assert_json "d['results'][0]['clip']['label'] == 'smoke_montage'"
$SHOT locate 0:01 --files no/such/file.mp4 2>/dev/null && fail "locate --files accepted a nonexistent file" || true

echo "[11/18] music (probe + mux + loop + staleness)" >&2
ffmpeg -nostdin -y -v error -f lavfi -i "sine=frequency=440:duration=12" "$MUSIC"
$SHOT music --probe "$MUSIC" --json 2>/dev/null \
    | assert_json "abs(d['results'][0]['duration_s'] - 12) < 0.2 and d['results'][0]['energy'] and d['results'][0]['integrated_lufs'] is not None"
# a --draft render blocks the mux (preview quality) until a final render
# (--force: a fresh FINAL render otherwise satisfies the --draft request = skip)
$SHOT montage --out "$MONT" --draft --force >/dev/null 2>&1 || fail "draft render before music failed"
$SHOT music "$MUSIC" --out "$FINAL" 2>/dev/null && fail "music muxed onto a --draft render" || true
$SHOT montage --out "$MONT" >/dev/null 2>&1 || fail "final render after draft failed"
$SHOT music "$MUSIC" --out "$FINAL" --json 2>/dev/null \
    | assert_json "abs(d['video_s'] - 6.5) < 0.5 and d['audio_s'] <= d['video_s'] + 0.1 and not d['looped']"
[ -f "$FINAL" ] || fail "final file missing"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$FINAL" \
    | grep -q audio || fail "final has no audio stream"
DUR_FIN=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$FINAL")
$PY -c "assert abs($DUR_FIN - 6.5) < 0.5, $DUR_FIN" || fail "final duration deviates from the montage"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['music']['applied']['state'] == 'fresh'"
ffmpeg -nostdin -y -v error -f lavfi -i "sine=frequency=330:duration=3" "$MUSIC_SHORT"
$SHOT music "$MUSIC_SHORT" --loop --out "$FINAL" --json 2>/dev/null \
    | assert_json "d['looped'] and d['gap_s'] < 0.6 and abs(d['audio_s'] - d['video_s']) < 0.6"

echo "[12/18] ui (read-only server: api joins, range, thumb, guards)" >&2
# pure helpers: Range parsing + path traversal guard
$PY -c "
from pathlib import Path
from pipeline import webui as w
assert w.parse_range(None, 100) is None
assert w.parse_range('bytes=0-99', 1000) == (0, 99)
assert w.parse_range('bytes=200-', 1000) == (200, 999)
assert w.parse_range('bytes=-50', 1000) == (950, 999)
assert w.parse_range('bytes=0-9,20-', 1000) is None       # multi-range -> full 200
assert w.parse_range('garbage', 1000) is None
try: w.parse_range('bytes=1000-', 1000); raise SystemExit(1)
except ValueError: pass                                   # start beyond file -> 416
assert w.safe_path('selects', Path('output')) is None     # directory, not a file
assert w.safe_path('../input/test_synthetic.mp4', Path('output')) is None
assert w.safe_path('%2e%2e/plan.jsonl', Path('output')) is None
" || fail "webui pure helpers"
PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline ui --port 0 --no-open --json >ui.json 2>/dev/null &
UI_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s ui.json ] && break; sleep 0.3; done
PORT=$($PY -c "import json; print(json.load(open('ui.json'))['port'])") || fail "shot ui did not report a port"
UI="http://127.0.0.1:$PORT"
curl -s "$UI/api/status" \
    | assert_json "d['selects'][0]['url'].startswith('/media/output/') and d['selects'][0]['thumb'].startswith('/thumb/') and d['selects'][0]['source_exists'] and d['inputs'][0]['selects'] and d['cuts']['main']['sequence'][0]['use_url']"
[ "$(curl -s -o /dev/null -w '%{http_code} %{size_download}' -H 'Range: bytes=0-99' "$UI/media/output/selects/test_synthetic_smoke.mp4")" = "206 100" ] \
    || fail "range request did not return 206 with 100 bytes"
curl -s -o thumb.jpg "$UI/thumb/test_synthetic_smoke.jpg"
[ -f output/ui-cache/thumbs/test_synthetic_smoke.jpg ] || fail "thumb did not land in output/ui-cache/thumbs/"
[ "$(curl -s --path-as-is -o /dev/null -w '%{http_code}' "$UI/media/output/../plan.jsonl")" = "404" ] \
    || fail "path traversal was not blocked"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$UI/api/status")" = "405" ] \
    || fail "POST was not refused"
kill $UI_PID
UI_PID=""

echo "[13/18] grade (stats + corrections + look + freshness + effect + LUT + preview)" >&2
# color stats: sane values, all keys, mtime cache on the second run
$SHOT grade --analyze "$SEL" --json 2>/dev/null \
    | assert_json "0 < d['results'][0]['stats']['mean_luma'] < 1 and d['results'][0]['frames_sampled'] >= 3 and all(k in d['results'][0]['stats'] for k in ('mean_sat', 'cast', 'cast_strength', 'clip_high_pct', 'clip_low_pct', 'luma_p5', 'luma_p95'))"
[ -f output/test_synthetic_smoke/color.json ] || fail "color.json cache missing"
$SHOT grade --analyze "$SEL" --json 2>grade.err >/dev/null
grep -q "cache" grade.err || fail "grade --analyze did not use the color.json cache"
# corrections: set (merge), range guard, neutral values remove keys; a _x* variant resolves to its select
$SHOT grade "$SEL2" --saturation 1.2 --json 2>/dev/null \
    | assert_json "d['results'][0]['grade'] == {'saturation': 1.2}"
$SHOT grade "$SEL2" --saturation 9 2>/dev/null && fail "grade accepted saturation out of range" || true
$SHOT grade "$SEL2" --exposure 0.3 --json 2>/dev/null \
    | assert_json "d['results'][0]['grade'] == {'saturation': 1.2, 'exposure': 0.3}"
$SHOT grade "$SEL2" --saturation 1 --exposure 0 --json 2>/dev/null \
    | assert_json "d['results'][0]['grade'] is None"
$SHOT grade "$SELX2" --contrast 1.1 --json 2>/dev/null \
    | assert_json "d['results'][0]['file'].endswith('smoke.mp4')"
$SHOT grade "$SEL" --clear --json >/dev/null 2>&1
# look: catalog + set + guard; grade change -> the fresh render goes stale
$SHOT grade --list-looks --json 2>/dev/null \
    | assert_json "'golden' in d['looks']"
$SHOT grade --look no-such-look 2>/dev/null && fail "grade accepted an unknown look" || true
$SHOT grade --look golden --json 2>/dev/null \
    | assert_json "d['grade'] == {'look': 'golden'}"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['render']['state'] == 'stale' and 'grade' in d['cuts']['main']['render']['reason']"
# graded crossfade render: snapshot in the record, fresh again, repeat run skips
$SHOT montage --out "$MONT" --json 2>/dev/null \
    | assert_json "d['graded'] is True and 'skipped' not in d"
$PY -c "from pipeline import manifest; r = manifest.get_cut()['render']; assert r.get('grade') and r['grade']['look'] == 'golden', r.get('grade')" \
    || fail "render record has no grade snapshot"
$SHOT montage --out "$MONT" --json 2>/dev/null \
    | assert_json "d.get('skipped') is True"
# --xfade 0 + grade: stream copy stays ungraded, loud warning, honestly grade-stale
$SHOT montage --out "$MONT" --xfade 0 --force --json 2>grade_xf0.err \
    | assert_json "d['graded'] is False"
grep -q "NOT applied" grade_xf0.err || fail "no warning about grades with --xfade 0"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['render']['state'] == 'stale'"
# effect end to end: saturation 0 on both selects -> the rendered film is grayscale
$SHOT grade --clear-look >/dev/null 2>&1
$SHOT grade "$SEL" "$SEL2" --saturation 0 --json 2>/dev/null \
    | assert_json "all(r['grade'] == {'saturation': 0.0} for r in d['results'])"
$SHOT montage --out "$MONT" --json 2>/dev/null | assert_json "d['graded'] is True"
$PY -c "
import cv2
cap = cv2.VideoCapture('$MONT'); cap.set(cv2.CAP_PROP_POS_FRAMES, 30)
ok, fr = cap.read(); assert ok
sat = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)[..., 1].mean() / 255
assert sat < 0.05, sat" || fail "saturation 0 grade did not desaturate the render"
# user LUT (lut3d + path escaping): a 2x2x2 cube zeroing blue
mkdir -p luts
printf 'LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n' > luts/zero-blue.cube
$SHOT grade "$SEL" "$SEL2" --clear --json >/dev/null 2>&1
$SHOT grade --look-lut no/such.cube 2>/dev/null && fail "grade accepted a missing LUT file" || true
$SHOT grade --look-lut luts/zero-blue.cube --json 2>/dev/null \
    | assert_json "d['grade'] == {'lut': 'luts/zero-blue.cube'}"
$SHOT montage --out "$MONT" --json >/dev/null 2>&1
$PY -c "
import cv2
cap = cv2.VideoCapture('$MONT'); cap.set(cv2.CAP_PROP_POS_FRAMES, 30)
ok, fr = cap.read(); assert ok
blue = float(fr[..., 0].mean())
assert blue < 10, blue" || fail "zero-blue LUT did not reach the render"
# normalize layer per source: stats re-measured THROUGH the input LUT (cache invalidated)
$SHOT grade --source "$SYN" --input-lut luts/zero-blue.cube --profile d-log --json 2>/dev/null \
    | assert_json "d['profile'] == 'd-log'"
$SHOT grade --analyze "$SEL" --json 2>/dev/null \
    | assert_json "d['results'][0]['normalize'] is not None and d['results'][0]['stats']['rgb_means'][2] == 0"
$SHOT grade --source "$SYN" --clear >/dev/null 2>&1
# before/after preview grid for the agent
$SHOT grade --preview --json 2>/dev/null \
    | assert_json "d['clips'] == 2 and d['graded'] == 2"
[ -f output/grade-preview/main.png ] || fail "grade preview PNG missing"
# back to an ungraded fresh render for the sections below
$SHOT grade --clear-look >/dev/null 2>&1
$SHOT montage --out "$MONT" >/dev/null 2>&1 || fail "ungraded re-render after grade tests failed"
$SHOT grade --json 2>/dev/null \
    | assert_json "d['look'] is None and d['corrections'] == [] and d['render']['state'] == 'fresh'"

echo "[14/18] trim (re-cut from source + variant refresh + staleness + cut guard)" >&2
# the select also plays in a second cut -> trim must warn that it changes all cuts
$SHOT sequence --cut smoke-b "$SEL" >/dev/null 2>&1
$SHOT trim "$SEL" 14 17 --note "smoke trim" --json 2>trim.err \
    | assert_json "d['range'] == [14.0, 17.0] and d['range_history'] == [[13.0, 18.0]] and d['variants_refreshed']"
grep -q "smoke-b" trim.err || fail "trim did not warn about a select shared between cuts"
DUR_X2=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SELX2")
$PY -c "assert abs($DUR_X2 - 1.5) < 0.3, $DUR_X2" || fail "x2 variant not refreshed after trim"
$SHOT status --json 2>/dev/null \
    | assert_json "d['cuts']['main']['render']['state'] == 'stale' and d['cuts']['main']['music']['applied']['state'] == 'stale'"
# remove also clears sequence entries from EVERY cut (everything here is isolated
# in the smoke dir, so nothing real needs restoring)
$PY -c "from pipeline import manifest; manifest.remove('$SEL'); manifest.remove('$SEL2')"
$PY -c "from pipeline import manifest; assert manifest.get_cut()['sequence'] == []" \
    || fail "remove did not clear the montage sequence"

echo "[15/18] config: custom input folder (isolated tmp)" >&2
TMP=$(mktemp -d)
mkdir -p "$TMP/output" "$TMP/sdcard"
touch "$TMP/sdcard/card.mp4"
run_tmp() { ( cd "$TMP" && PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline "$@" 2>/dev/null ); }
run_tmp config --input-dir sdcard --json \
    | assert_json "d['input_dir'] == 'sdcard' and d['input_dir_source'] == 'config.json'"
run_tmp status --json \
    | assert_json "d['input_dir'] == 'sdcard' and d['inputs'][0]['file'].endswith('card.mp4')"
run_tmp config --input-dir no-such-dir --json && fail "config accepted a nonexistent directory" || true
( cd "$TMP" && SHOT_INPUT_DIR=elsewhere PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline config --json 2>/dev/null ) \
    | assert_json "d['input_dir'] == 'elsewhere' and d['input_dir_source'] == 'env SHOT_INPUT_DIR'"
run_tmp config --reset --json \
    | assert_json "d['input_dir'] == 'input' and d['input_dir_source'] == 'default'"
[ ! -f "$TMP/config.json" ] || fail "reset did not remove config.json"
# .env loader: variables from the file land in env, but the real env wins
printf '# comment\nSHOT_INPUT_DIR=sdcard\n' > "$TMP/.env"
run_tmp config --json \
    | assert_json "d['input_dir'] == 'sdcard' and d['input_dir_source'] == 'env SHOT_INPUT_DIR'"
( cd "$TMP" && SHOT_INPUT_DIR=elsewhere PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline config --json 2>/dev/null ) \
    | assert_json "d['input_dir'] == 'elsewhere'" \
    || fail "real env did not win over .env"
rm -rf "$TMP"

echo "[16/18] archive + restore (isolated tmp)" >&2
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

echo "[17/18] publish (thumbnail + description, isolated tmp)" >&2
TMP=$(mktemp -d)
mkdir -p "$TMP/output"
ffmpeg -nostdin -y -v error -f lavfi -i "testsrc2=size=1920x1080:rate=24:duration=2" "$TMP/src.mp4"
printf 'BOILER\nLINE2\n' > "$TMP/tpl.txt"
printf 'Specific part.\n' > "$TMP/spec.txt"
run_pub() { ( cd "$TMP" && SHOT_PUBLISH_TEMPLATE=tpl.txt PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m pipeline "$@" 2>/dev/null ); }
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

echo "[18/18] validate (schema contract + negative test)" >&2
# every mutator has already passed save-side validation by now (selects, tags,
# sequence, render, music, trim, remove) — this checks the files on disk
$SHOT validate --json 2>/dev/null \
    | assert_json "d['ok'] is True and d['checked'] >= 3"
# a stray field must fail (additionalProperties: false)
$PY -c "import json; p='output/project.json'; d=json.load(open(p)); d['bogus']=1; json.dump(d, open(p,'w'))"
$SHOT validate >/dev/null 2>&1 && fail "validate accepted an invalid manifest" || true
$PY -c "import json; p='output/project.json'; d=json.load(open(p)); d.pop('bogus'); json.dump(d, open(p,'w'))"
$SHOT validate >/dev/null 2>&1 || fail "validate still failing after restoring the manifest"

echo "SMOKE OK" >&2
