"""Export only generated demo art for visual QA and the README."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from turing_models import PRESETS, colour_field, demo_image, evolve, prepare_gray, portrait
from turing_services import postcard

root = Path(__file__).resolve().parents[1]
fig, axes = plt.subplots(2, 3, figsize=(12, 7), facecolor='#f2f6fa')
axes.flat[0].imshow(demo_image())
axes.flat[0].set_title('Generated demo portrait')
gray = prepare_gray(demo_image(), 160)
for ax, (key, p) in zip(list(axes.flat)[1:], PRESETS.items()):
    _, fields = evolve(gray, p, seed=19, snapshots=2)
    ax.imshow(colour_field(fields[-1], p))
    ax.set_title(f'{key} · {p.model}')
    if key == 'labyrinth':
        demo_portrait = portrait(demo_image(), fields[-1], p, .58)
        email_collage = postcard(demo_portrait, key, original=demo_image())
for ax in axes.flat:
    ax.axis('off')
fig.tight_layout()
(root/'assets').mkdir(exist_ok=True)
fig.savefig(root/'assets'/'preset_gallery.png', dpi=130)
email_collage.save(root/'assets'/'demo_email_collage.png')
plt.close(fig)
print('Generated-only gallery and email collage: assets/preset_gallery.png, assets/demo_email_collage.png')
