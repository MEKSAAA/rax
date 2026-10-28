"""Bounded Pillow compatibility for the benchmark's packaged large eraser texture.

The shipped 04 eraser embeds a 277,805,556-pixel image. Keep the original pixels
and geometry: no resize, recolor, or collision-mesh substitution. The temporary
limit is confined to the trusted simulator's environment construction and always
restored. Pillow still rejects images over 280,000,000 pixels.
"""
from contextlib import contextmanager


@contextmanager
def benchmark_texture_loading():
    from PIL import Image
    original = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 140_000_000
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = original
