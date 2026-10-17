"""Launcher script (used by PyInstaller and for running from source)."""

import sys

from photo_review.app import main

if __name__ == "__main__":
    sys.exit(main())
