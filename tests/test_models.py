"""Numerical behaviour and meaningful preset differences, not rendering trivia."""
import numpy as np
import pytest
from scipy.ndimage import label
from turing_models import PRESETS, Cancelled, Preset, demo_image, diffusion, evolve, initial_field, parameters, prepare_gray


@pytest.fixture(scope="module")
def gray():
    return prepare_gray(demo_image(), 80)


@pytest.mark.parametrize("key", list(PRESETS))
def test_presets_deterministic_finite_and_evolving(gray, key):
    steps, frames = evolve(gray, PRESETS[key], seed=42, snapshots=4)
    _, again = evolve(gray, PRESETS[key], seed=42, snapshots=4)
    assert steps[0] == 0 and steps[-1] == PRESETS[key].steps
    assert all(np.isfinite(f).all() for f in frames)
    assert np.array_equal(frames[-1], again[-1])
    assert not np.allclose(frames[0], frames[-1])
    assert frames[-1].std() > .02  # avoid a uniform field advertised as a pattern


def test_diffusion_smooths_and_preserves_constant(gray):
    assert diffusion(gray, 100).var() < gray.var()
    assert np.allclose(diffusion(np.ones((30, 30)), 20), 1)
    assert np.array_equal(diffusion(gray, 0), gray)


def test_reduced_amplifies_seed_noise_at_selected_scale():
    gray = np.full((128, 128), .5, dtype=np.float32)
    _, frames = evolve(gray, PRESETS['labyrinth'], seed=2, snapshots=2)
    assert frames[-1].std() > frames[0].std()*2
    spectrum = abs(np.fft.fftshift(np.fft.fft2(frames[-1]-frames[-1].mean())))**2
    y, x = np.indices(gray.shape)
    radius = np.sqrt((x-64)**2+(y-64)**2).astype(int)
    radial = np.bincount(radius.ravel(), weights=spectrum.ravel()) / np.maximum(np.bincount(radius.ravel()), 1)
    assert 2 < np.argmax(radial) < 50


def test_anisotropy_and_scale_change_the_structure():
    gray = np.full((128, 128), .5, dtype=np.float32)
    _, stripes = evolve(gray, PRESETS['stripes'], seed=2, snapshots=2)
    dy, dx = np.gradient(stripes[-1])
    assert dx.std() > 2*dy.std()
    _, fine = evolve(gray, PRESETS['labyrinth'], seed=2, size=.6, snapshots=2)
    _, broad = evolve(gray, PRESETS['labyrinth'], seed=2, size=1.8, snapshots=2)
    assert sum(g.var() for g in np.gradient(fine[-1])) > sum(g.var() for g in np.gradient(broad[-1]))*2


def test_bias_produces_isolated_holes(gray):
    _, holes = evolve(gray, PRESETS['holes'], seed=2, snapshots=2)
    assert (holes[-1] > 0).mean() > .6
    assert label(holes[-1] < 0)[1] > 3


def test_seed_and_cancellation(gray):
    assert not np.array_equal(initial_field(gray, 2), initial_field(gray, 3))
    with pytest.raises(Cancelled):
        evolve(gray, PRESETS['labyrinth'], cancel=lambda: True)
    research = Preset('research', 'gray_scott', dict(feed=.026, kill=.055, du=.16, dv=.08, dt=1.), 'sunset', 20, True)
    p = parameters(research, 1.8)
    assert p['dt']*p['du'] <= .25


def test_only_the_requested_families_are_available():
    assert set(PRESETS) == {'stripes', 'labyrinth', 'holes', 'fine'}
