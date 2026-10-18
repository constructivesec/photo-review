"""Launcher used by PyInstaller."""

import sys

from photo_review.app import main

if __name__ == "__main__":
    sys.exit(main())
