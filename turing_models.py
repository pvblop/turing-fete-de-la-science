"""CPU pattern models shared by the kiosk and notebook; no camera or file I/O."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter, laplace


@dataclass(frozen=True)
class Preset:
    """Names/explanations are translation keys, parameters describe dynamics."""
    key: str
    model: str
    parameters: Mapping[str, float]
    palette: str
    steps: int
    experiment: bool = False


PALETTES = {
    "ocean": ((13, 34, 66), (43, 211, 197)),
    "sunset": ((75, 25, 94), (255, 197, 85)),
    "forest": ((17, 63, 51), (221, 241, 139)),
    "mono": ((12, 16, 28), (250, 250, 250)),
}
BASE = dict(r=-0.15, gamma=1.2, cubic=1.0, sigma=1.3, ratio=4.0,
            bias=0.0, anisotropy=1.0, dt=0.2)
PRESETS = {
    "stripes": Preset("stripes", "reduced", dict(BASE, anisotropy=3.5), "ocean", 150),
    "labyrinth": Preset("labyrinth", "reduced", BASE.copy(), "ocean", 150),
    "holes": Preset("holes", "reduced", dict(BASE, bias=.22), "forest", 210),
    "fine": Preset("fine", "reduced", dict(BASE, sigma=.7), "sunset", 150),
}


class Cancelled(Exception):
    """A superseded request should exit without publishing its result."""


def demo_image(size: int = 600) -> Image.Image:
    """Draw a friendly robot portrait, without external assets or private data."""
    im = Image.new("RGB", (size, size), (223, 244, 242))
    d = ImageDraw.Draw(im)
    def box(coords):
        return tuple(int(c * size / 600) for c in coords)
    d.ellipse(box((55, 400, 545, 880)), fill=(34, 147, 155))
    d.rounded_rectangle(box((125, 100, 475, 435)), radius=int(size*.07), fill=(245, 187, 81), outline=(24, 49, 74), width=max(2, size//70))
    d.line(box((300, 100, 300, 55)), fill=(24, 49, 74), width=max(2, size//60))
    d.ellipse(box((277, 25, 323, 70)), fill=(226, 98, 99))
    for x in (210, 390):
        d.ellipse(box((x-40, 200, x+40, 280)), fill=(24, 49, 74))
        d.ellipse(box((x-14, 210, x+4, 228)), fill="white")
    d.arc(box((220, 270, 380, 370)), 0, 180, fill=(24, 49, 74), width=max(2, size//50))
    return im


def prepare_gray(image: Image.Image, resolution: int = 224) -> np.ndarray:
    """Center-crop and resize; luminance is the common initial image."""
    w, h = image.size
    side = min(w, h)
    crop = image.crop(((w-side)//2, (h-side)//2, (w+side)//2, (h+side)//2))
    return np.asarray(crop.convert("L").resize((resolution, resolution), Image.Resampling.LANCZOS), dtype=np.float32) / 255.


def initial_field(gray: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centered = gray - gray.mean()
    return (centered / max(float(centered.std()), 1e-6) * .10
            + rng.normal(0, .07, gray.shape)).astype(np.float32)


def diffusion(gray: np.ndarray, time: float, diffusivity: float = 1.) -> np.ndarray:
    """Heat-equation Gaussian solution with reflecting boundary approximation."""
    if time < 0 or diffusivity < 0:
        raise ValueError("Nonnegative diffusion time and diffusivity required")
    return gaussian_filter(gray, np.sqrt(2 * diffusivity * time), mode="reflect") if time else gray.copy()


def parameters(preset: Preset, size: float = 1.) -> dict[str, float]:
    """Size changes interaction ranges or diffusion lengths, not an image filter."""
    if not .6 <= size <= 1.8:
        raise ValueError("Size must be between 0.6 and 1.8")
    p = dict(preset.parameters)
    if preset.model == "reduced":
        p["sigma"] *= size
    elif preset.model == "gray_scott":
        p["du"] *= size * size
        p["dv"] *= size * size
        # Explicit five-point diffusion CFL: dt * Dmax <= 1/4.
        p["dt"] = min(p["dt"], .22 / p["du"])
    else:
        raise ValueError("Unknown model")
    return p


def evolve(gray: np.ndarray, preset: Preset, seed: int = 0, size: float = 1.,
           snapshots: int = 25, steps: int | None = None,
           cancel: Callable[[], bool] = lambda: False,
           progress: Callable[[float], None] = lambda _fraction: None) -> tuple[list[int], list[np.ndarray]]:
    """Integrate and retain a bounded sequence. No Tk calls occur here.

    Reduced: u_t = r*u - c*u^3 + gamma*(G_a*u-G_i*u) + bias.
    Gray–Scott: U_t=D_u lap U-U V^2+F(1-U),
                V_t=D_v lap V+U V^2-(F+k)V.
    Both use reflecting boundaries. Anisotropy is an explicit model assumption.
    """
    if gray.ndim != 2 or not np.isfinite(gray).all():
        raise ValueError("A finite two-dimensional image is required")
    p = parameters(preset, size)
    total = preset.steps if steps is None else steps
    if total < 1 or not 2 <= snapshots <= 64:
        raise ValueError("Positive steps and 2–64 snapshots required")
    wanted = set(np.linspace(0, total, min(snapshots, total+1), dtype=int))
    indices, frames = [], []
    u = initial_field(gray, seed)
    if preset.model == "gray_scott":
        rng = np.random.default_rng(seed)
        # Finite-amplitude seeds: Gray–Scott is not necessarily a linear
        # Turing instability of its homogeneous feed state.
        seeds = gaussian_filter(rng.random(gray.shape).astype(np.float32), 1.)
        mask = seeds > np.percentile(seeds, 82)
        v = np.where(mask, .25 + .12 * gray, 0.).astype(np.float32)
        u = np.where(mask, .5, 1.).astype(np.float32)
    for step in range(total+1):
        if step % 8 == 0:
            if cancel():
                raise Cancelled()
            progress(step / total)
        if step in wanted:
            indices.append(step)
            frames.append((v if preset.model == "gray_scott" else u).copy())
        if step == total:
            break
        if preset.model == "reduced":
            sigma = (p["sigma"] * p["anisotropy"], p["sigma"])
            a = gaussian_filter(u, sigma, mode="reflect")
            b = gaussian_filter(u, tuple(s*p["ratio"] for s in sigma), mode="reflect")
            u += p["dt"] * (p["r"]*u - p["cubic"]*u**3 + p["gamma"]*(a-b) + p["bias"])
        else:
            uvv = u*v*v
            next_u = u + p["dt"]*(p["du"]*laplace(u, mode="reflect")-uvv+p["feed"]*(1-u))
            v += p["dt"]*(p["dv"]*laplace(v, mode="reflect")+uvv-(p["feed"]+p["kill"])*v)
            u = next_u
    if not np.isfinite(frames[-1]).all():
        raise ArithmeticError("Unstable model parameters")
    progress(1.)
    return indices, frames


def colour_field(field: np.ndarray, preset: Preset, palette: str | None = None) -> Image.Image:
    """Fixed model-specific scale avoids artificial frame-by-frame contrast growth."""
    z = np.clip(field / .42, 0, 1) if preset.model == "gray_scott" else .5 + .5 * np.tanh(field*3.)
    low, high = (np.array(c, dtype=np.float32) for c in PALETTES[palette or preset.palette])
    rgb = low + z[..., None]*(high-low)
    return Image.fromarray(np.uint8(np.clip(rgb, 0, 255)))


def portrait(original: Image.Image, field: np.ndarray, preset: Preset,
             intensity: float = .6, palette: str | None = None, output_size: int = 900) -> Image.Image:
    """Blend for recognisability; this display choice is separate from dynamics."""
    w, h = original.size
    s = min(w, h)
    photo = original.crop(((w-s)//2, (h-s)//2, (w+s)//2, (h+s)//2)).convert("RGB")
    photo = photo.resize((output_size, output_size), Image.Resampling.LANCZOS)
    pattern = colour_field(field, preset, palette).resize(photo.size, Image.Resampling.BICUBIC)
    if palette == "mono":
        photo = photo.convert("L").convert("RGB")
    return Image.blend(photo, pattern, float(np.clip(intensity, 0, 1)))
