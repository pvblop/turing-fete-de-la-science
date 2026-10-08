"""Rebuild the public, private-file-free notebook from the shared models."""
from pathlib import Path
import nbformat as nbf

cells = []
def md(text):
    cells.append(nbf.v4.new_markdown_cell(text.strip()))
def code(text):
    cells.append(nbf.v4.new_code_cell(text.strip()))

md(r"""
# Photo → smoothing or pattern growth

This notebook accompanies **La fabrique des motifs / The pattern factory**.
Run from top to bottom with the project environment. All illustrations use a
locally drawn robot: no internet, private photos, webcam, SMTP or GPU is needed.
The equations below are implemented in `turing_models.py`, also used by the app.

**Takeaway:** complex patterns can emerge from simple local rules. Not every
natural spot, stripe or shell marking has a Turing mechanism.
""")
code("""
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from dataclasses import replace
from turing_models import (PRESETS, demo_image, prepare_gray, initial_field,
                           diffusion, evolve, colour_field, portrait, parameters)

SEED = 19
SIZE = 128
photo = demo_image()
gray = prepare_gray(photo, SIZE)
plt.rcParams.update({'figure.figsize': (8, 4), 'font.size': 11})
plt.imshow(photo)
plt.title('Generated example: no personal files')
plt.axis('off')
plt.show()
""")
md("""
## Optional photograph

Leave `CUSTOM_IMAGE = None` for the public demonstration. To use your own image,
set a relative path yourself. Never save/share notebook outputs containing
visitor photos. This optional cell is the only place that can read an image file.
""")
code("""
CUSTOM_IMAGE = None
if CUSTOM_IMAGE is not None:
    from PIL import Image
    with Image.open(Path(CUSTOM_IMAGE)) as source:
        photo = source.convert('RGB').copy()
    gray = prepare_gray(photo, SIZE)
""")
md(r"""
## 1. Ordinary diffusion: remove differences

$$\partial_t I = D\nabla^2 I,\qquad I(t)=G_{\sqrt{2Dt}}*I(0).$$

The Gaussian solution is exact on an infinite plane. Here SciPy's reflecting
boundary convolution approximates zero flux at the finite image edges. It
smooths structure; it does not grow a preferred nonzero spatial frequency.
These are dimensionless model times, not seconds of biological development.
""")
code("""
times = [0, 2, 10, 50, 180]
diffused = [diffusion(gray, t) for t in times]
fig, axes = plt.subplots(1, len(times), figsize=(14, 3))
for ax, frame, t in zip(axes, diffused, times):
    ax.imshow(frame, cmap='gray', vmin=0, vmax=1)
    ax.set_title(f't = {t}')
    ax.axis('off')
plt.tight_layout()
plt.show()
assert diffused[-1].var() < gray.var()
""")
md(r"""
## 2. The original reduced one-field approximation

$$\partial_t u = r u-c u^3+\gamma(G_{\sigma_a}*u-G_{\sigma_i}*u)+b,
\qquad \sigma_i>\sigma_a.$$

This phenomenological, **nonlocal single-field** equation is not a literal
two-chemical Turing system. Short-range reinforcement and broader suppression
select a length scale; the cubic term limits amplitude. A photograph supplies
the centred initial field, plus seeded noise. `bias=0` gives the original
symmetry; a positive bias favours one sign, leaving holes of the other sign.

`stripes` uses **anisotropic interaction ranges** (greater spread along one
axis), an explicit assumption that aligns bands. `fine` changes Gaussian widths.
The size control scales the interaction ranges for every remaining preset.
These changes are in the equations, not arbitrary filters.

For isotropic kernels, the linear growth rate of a Fourier mode is
$$\lambda(q)=r+\gamma[e^{-\sigma_a^2q^2/2}-e^{-\sigma_i^2q^2/2}].$$
It can be positive at a finite wave number even though the uniform mode decays.
The bias changes the equilibrium about which one would linearise.
""")
code("""
p = PRESETS['labyrinth']
steps, fields = evolve(gray, p, SEED, snapshots=6)
fig, axes = plt.subplots(1, len(fields), figsize=(15, 3))
for ax, step, field in zip(axes, steps, fields):
    ax.imshow(field, cmap='coolwarm', vmin=-1.1, vmax=1.1)
    ax.set_title(f'step {step}')
    ax.axis('off')
plt.tight_layout()
plt.show()

q = np.linspace(0, 4, 400)
params = p.parameters
growth = params['r'] + params['gamma']*(np.exp(-params['sigma']**2*q**2/2)
                 - np.exp(-(params['sigma']*params['ratio'])**2*q**2/2))
plt.plot(q, growth)
plt.axhline(0, color='black', lw=.8)
plt.xlabel('Wave number q (inverse grid pixels)')
plt.ylabel('Linear growth rate about u=0')
plt.title('A preferred scale grows; the uniform mode does not')
plt.show()
""")
md(r"""
## 3. Classical two-species Turing systems: the distinction

$$\partial_t U=D_U\nabla^2U+f(U,V),\qquad
\partial_t V=D_V\nabla^2V+g(U,V).$$

Two concentrations react **locally** and diffuse. A classical Turing instability
destabilises a homogeneous equilibrium that was stable **without diffusion**.
The kiosk instead uses one nonlocal field with two Gaussian interaction ranges.
It illustrates finite-scale pattern selection without claiming to simulate two
actual chemicals in an animal's skin.

Here is a linear stability example, rather than another portrait preset. The
reaction Jacobian has trace −2 and determinant 1, so its uniform equilibrium
is stable. With diffusivities 0.05 and 1, some nonzero wave numbers grow.
This describes small perturbations only; nonlinear terms would be needed to
saturate growth and determine a final biological-looking morphology.

The exhibit offers four reduced-model families. The Gray–Scott integrator is
retained in the model module as an extension point, with no kiosk presets.
""")
code("""
J = np.array([[1., -2.], [2., -3.]])
D = np.diag([.05, 1.])
wave_numbers = np.linspace(0, 7, 300)
rates = np.array([np.linalg.eigvals(J - D*q*q).real.max() for q in wave_numbers])
assert np.linalg.eigvals(J).real.max() < 0
assert rates.max() > 0
plt.plot(wave_numbers, rates)
plt.axhline(0, color='black', lw=.8)
plt.xlabel('Wave number q')
plt.ylabel('Largest linear growth rate')
plt.title('Two-field example: stable uniform state, growing finite scale')
plt.show()
""")
md("""
## 4. Every live-exhibit preset

Names, equations and colour palettes are shared with the kiosk. Thumbnails are
locally generated using seed 19; there are no downloaded or copyrighted images.
Rendering uses a fixed model-specific scale throughout the animation, avoiding
the illusion of growth caused by renormalising every frame.
""")
code("""
results = {}
fig, axes = plt.subplots(2, 2, figsize=(10, 8))
for ax, (key, preset) in zip(axes.flat, PRESETS.items()):
    _, frames = evolve(gray, preset, SEED, snapshots=3)
    results[key] = frames[-1]
    ax.imshow(colour_field(frames[-1], preset))
    ax.set_title(f'{key}: {preset.model}')
    ax.axis('off')
    print(key, dict(preset.parameters), 'steps=', preset.steps)
plt.tight_layout()
plt.show()
assert set(results) == set(PRESETS)
""")
md("""
## 5. Same initial photograph, different rules

The left and right derive from the same luminance image. The pattern field is
centred/scaled and has a small seeded perturbation. Diffusion smooths the
photograph; the reduced model amplifies selected spatial scales. A separate
portrait blend preserves facial recognition: it is a display choice, not an
extra biological mechanism. The app's Photo ← Blend → Pattern control changes
that blend. Both kiosk panels use the same square crop and display size.
""")
code("""
fig, axes = plt.subplots(1, 4, figsize=(14, 4))
axes[0].imshow(gray, cmap='gray', vmin=0, vmax=1)
axes[0].set_title('Common initial image')
axes[1].imshow(diffusion(gray, 180), cmap='gray', vmin=0, vmax=1)
axes[1].set_title('Diffusion')
axes[2].imshow(colour_field(results['labyrinth'], PRESETS['labyrinth'], 'mono'))
axes[2].set_title('Pattern-forming interactions')
axes[3].imshow(portrait(photo, results['labyrinth'], PRESETS['labyrinth'], .58, output_size=256))
axes[3].set_title('Portrait display: 58% blend')
for ax in axes:
    ax.axis('off')
plt.tight_layout()
plt.show()
""")
md("""
## 6. What changes bands, holes and scale?

The reduced equation breaks sign symmetry with `bias`, aligns bands with
`anisotropy`, and changes preferred scale with `sigma`. These are different
model mechanisms. The comparison below holds the initial image and seed fixed.
No threshold filter creates these forms.
""")
code("""
fig, axes = plt.subplots(2, 2, figsize=(10, 8))
experiments = [
    ('Isotropic labyrinth', PRESETS['labyrinth']),
    ('Directional stripes', PRESETS['stripes']),
    ('Biased holes', PRESETS['holes']),
    ('Shorter interaction range', PRESETS['fine']),
]
for ax, (title, preset) in zip(axes.flat, experiments):
    ax.imshow(colour_field(results[preset.key], preset))
    ax.set_title(title)
    ax.axis('off')
plt.tight_layout()
plt.show()

# Repeat a seed to check reproducibility; vary a scientific parameter to explore.
custom = replace(PRESETS['labyrinth'], parameters=dict(PRESETS['labyrinth'].parameters, ratio=2.5))
_, custom_frames = evolve(gray, custom, seed=SEED, snapshots=2)
plt.imshow(colour_field(custom_frames[-1], custom))
plt.title('What if inhibition travelled only 2.5 times farther?')
plt.axis('off')
plt.show()
""")
md("""
## 7. Live-exhibit settings and caveats

- Kiosk default: 224×224 simulation, 25 stored snapshots, seed 19, size 1,
  blend 0.58. The four reduced presets use 150–210 steps.
- Sliders are debounced by 240 ms. One model worker discards superseded jobs.
- Slow/Normal/Fast changes playback delay (600/240/80 ms), **not** integration dt.
- Email attaches a side-by-side collage of the original photo and Turing portrait,
  with two 900×900 panels. Upsampling does not create higher-resolution simulated detail.
- On slower machines, start with `--resolution 160` or `128`. No GPU is needed.
- Increasing the blend towards Pattern can obscure a portrait. It shows the
  evolving field seeded by the photograph, rather than retaining photo detail.
- Pigmentation and development have many interacting causes, including genetics,
  mechanics and growth. Similar appearance alone does not establish causation.
- The three-step activation/inhibition diagram explains how nearby growth and
  wider suppression can separate patches. Its slider changes the illustrative
  spacing; it is a conceptual analogy, not an additional chemical simulation.

## References

- A. M. Turing (1952), *The chemical basis of morphogenesis*, Phil. Trans. R. Soc.
  B 237, 37–72. [doi:10.1098/rstb.1952.0012](https://doi.org/10.1098/rstb.1952.0012)
- J. E. Pearson (1993), *Complex Patterns in a Simple System*, Science 261,
  189–192. [Author manuscript](https://arxiv.org/abs/patt-sol/9304003).
- [MIT Gray–Scott model equations](https://groups.csail.mit.edu/mac/projects/amorphous/GrayScott/).
""")

notebook = nbf.v4.new_notebook(cells=cells, metadata={
    'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
    'language_info': {'name': 'python', 'version': '3.11'},
})
nbf.validate(notebook)
target = Path(__file__).resolve().parents[1]/'turing_photo_demo.ipynb'
nbf.write(notebook, target)
print('Rebuilt notebook:', len(cells), 'cells; no stored personal outputs')
