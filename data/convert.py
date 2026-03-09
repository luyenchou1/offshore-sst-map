"""Unit conversion and visual upsampling helpers."""

import numpy as np
from scipy.ndimage import zoom


def to_fahrenheit_whole(arr: np.ndarray, units_hint: str) -> np.ndarray:
    """Convert an SST array to whole-degree Fahrenheit."""
    data = arr.astype("float32")
    u = (units_hint or "").lower()
    if u.startswith("k"):
        f = (data - 273.15) * 9 / 5 + 32
    elif u.startswith("c") or "degree_c" in u or "degrees_c" in u:
        f = data * 9 / 5 + 32
    elif u.startswith("f"):
        f = data
    else:
        # Default: assume Kelvin
        f = (data - 273.15) * 9 / 5 + 32
    f = np.where(np.isfinite(f), f, np.nan)
    return np.round(f, 0)


def upsample_visual(arr: np.ndarray, factor: int) -> np.ndarray:
    """Upsample a 2-D array by *factor* using linear interpolation."""
    if factor <= 1:
        return arr
    return zoom(arr, (factor, factor), order=1)
