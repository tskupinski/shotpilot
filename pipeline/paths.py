"""Layout of the output/ directory — the only place defining artifact paths.

Paths are RELATIVE to the current working directory (project = CWD);
`shot archive`/`shot restore` move the whole OUTPUT between the project and ARCHIVE.
The input folder is separate (per-machine `config.input_dir()`), because it
is not archived. Changing the output layout = editing ONLY here.
"""

from pathlib import Path

OUTPUT = Path("output")
ARCHIVE = Path("archive")

MANIFEST = OUTPUT / "project.json"
CUTS = OUTPUT / "cuts"
SELECTS = OUTPUT / "selects"
SMOOTH_CACHE = OUTPUT / "smooth-cache"
MUSIC = OUTPUT / "music"
PUBLISH = OUTPUT / "publish"
UI_CACHE = OUTPUT / "ui-cache"


def video_dir(stem: str) -> Path:
    """Artifact directory per source video: output/<stem>/ (summary, contact,
    review, segments, motion.csv, frames/)."""
    return OUTPUT / stem


def cut_render(cut: str) -> Path:
    """The cut's render: output/cuts/<name>.mp4 ("main" is no exception — uniform)."""
    return CUTS / f"{cut}.mp4"


def cut_final(cut: str) -> Path:
    """The cut's film with music: output/cuts/<name>-final.mp4."""
    return CUTS / f"{cut}-final.mp4"
