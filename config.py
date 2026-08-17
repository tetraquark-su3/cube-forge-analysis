"""
Shared configuration for the cube-analysis pipeline.

All scripts in this repo import FORGE_DIR / DECKS_BASE_DIR from here instead of
hardcoding a path, so the whole pipeline can be pointed at a different Forge
installation or cube project with a single edit (or environment variables,
no edit needed at all).

Usage:
    export FORGE_DIR=/path/to/forge-installer-x.y.z
    export CUBE_DECKS_DIR=/path/to/forge-installer-x.y.z/res/my_cube_decks   # optional,
        # defaults to $FORGE_DIR/res/my_cube_decks if not set

If you don't want to use environment variables, just edit the two defaults below.
"""
import os

FORGE_DIR = os.environ.get("FORGE_DIR", os.path.expanduser("~/forge"))
DECKS_BASE_DIR = os.environ.get(
    "CUBE_DECKS_DIR", os.path.join(FORGE_DIR, "res/my_cube_decks")
)
