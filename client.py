#!/usr/bin/env python3
"""Raccourci : `python client.py stats` continue de marcher sans venv.
Le vrai code est dans src/waalaxy_api/client.py (console script `waalaxy` dans le venv)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from waalaxy_api.client import main  # noqa: E402

if __name__ == "__main__":
    main()
