#!/usr/bin/env python3
import os
os.environ["GDK_BACKEND"] = "x11"

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app import TranslatorApp

if __name__ == "__main__":
    TranslatorApp().run()
