"""glance-bookmarks-editor: a small web editor for the Glance Home page tiles, countdown dates and icons."""

import os

_here = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(_here, "..", "VERSION"), encoding="utf-8") as f:
        __version__ = f.read().strip()
except OSError:
    __version__ = "dev"
