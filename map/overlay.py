"""SST array to base64 PNG for dash-leaflet ImageOverlay."""

import base64
import io

import numpy as np
from PIL import Image
import matplotlib.colors as mcolors

from map.colorscale import SST_COLORS


def _build_mpl_cmap():
    return mcolors.LinearSegmentedColormap.from_list("sst", SST_COLORS, N=256)


def array_to_rgba(arrF: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """Vectorized color mapping — replaces the old per-pixel Python loop."""
    cmap = _build_mpl_cmap()
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    normalized = norm(arrF)
    rgba = (cmap(normalized) * 255).astype(np.uint8)

    # Set NaN pixels to fully transparent
    nan_mask = np.isnan(arrF)
    rgba[nan_mask] = [0, 0, 0, 0]
    return rgba


def sst_to_base64_png(
    arrF: np.ndarray, vmin: float, vmax: float
) -> str:
    """Convert an SST array to a data-URI PNG for dl.ImageOverlay."""
    rgba = array_to_rgba(arrF, vmin, vmax)
    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"
