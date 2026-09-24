"""
Prove the tray-balloon notification path works on this machine.

The widget only raises one when the price actually flips, which cannot be
triggered on demand, so this exercises the same pystray call directly:

    .venv\\Scripts\\python.exe tools\\notify-test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pystray  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def dot(colour: tuple[int, int, int]) -> Image.Image:
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((3, 3, size - 4, size - 4), fill=(*colour, 255))
    draw.ellipse((19, 19, size - 20, size - 20), fill=(11, 18, 28, 235))
    draw.ellipse((27, 27, size - 28, size - 28), fill=(*colour, 255))
    return image


icon = pystray.Icon(
    "deepseek-balance-notify-test",
    dot((82, 221, 143)),
    "notification test",
    pystray.Menu(pystray.MenuItem("Quit", lambda *_: icon.stop())),
)
icon.run_detached()
time.sleep(0.8)
icon.notify(
    "API calls will fail until you top up.",
    "DeepSeek balance is empty",
)
print("notification raised — you should see a Windows notification")
time.sleep(6)
icon.stop()
print("done")
