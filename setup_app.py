from __future__ import annotations

import tomllib
from pathlib import Path
import zlib

from setuptools import setup

ROOT = Path(__file__).resolve().parent
PYPROJECT = ROOT / "pyproject.toml"

# py2app expects zlib to look like a normal extension module when freezing.
# On this Python build it can be a builtin without __file__, which breaks the
# standalone bundle step, so provide a stable path marker for py2app.
if getattr(zlib, "__file__", None) is None:
    zlib.__file__ = "/tmp/keypulse-libz.dylib"


def read_version() -> str:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


APP = [str(ROOT / "app_main.py")]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "KeyPulse",
        "CFBundleDisplayName": "KeyPulse",
        "CFBundleIdentifier": "com.keypulse.app",
        "CFBundleVersion": read_version(),
        "CFBundleShortVersionString": read_version(),
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "KeyPulse needs to observe app activity to summarize your day.",
        "NSScreenCaptureDescription": "KeyPulse uses screen capture for OCR-based activity insights.",
    },
    "packages": ["keypulse", "rich"],
    "includes": [
        "click",
        "pydantic",
        "pydantic_settings",
        "AppKit",
        "Quartz",
        "Vision",
        "objc",
        "Foundation",
    ],
    "excludes": ["tkinter", "matplotlib", "pytest"],
    "site_packages": True,
    "optimize": 0,
}


setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
