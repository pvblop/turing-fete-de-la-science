"""Bounded background work, camera ownership and memory-only email transport."""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from importlib.resources import files
import io
import os
import re
import smtplib
import ssl
import threading
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from turing_models import Cancelled, PRESETS, colour_field, demo_image, evolve, prepare_gray, portrait
from turing_i18n import tr

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# DejaVu ships with our Matplotlib dependency and supports the French captions.
# Load the font once; postcard creation and email transport then use RAM only.
_POSTCARD_FONT = files("matplotlib").joinpath("mpl-data", "fonts", "ttf", "DejaVuSans.ttf").read_bytes()


def _postcard_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(io.BytesIO(_POSTCARD_FONT), size)


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class SMTPConfig:
    """Secrets are read only from the operator's environment, never logged."""
    def __init__(self) -> None:
        self.host = os.getenv("SMTP_HOST", "").strip()
        try:
            self.port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError:
            self.port = 0
        self.user = os.getenv("SMTP_USER", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.sender = os.getenv("SMTP_FROM", self.user).strip()
        self.use_ssl = env_bool("SMTP_USE_SSL", False)
        self.use_tls = env_bool("SMTP_USE_TLS", not self.use_ssl)

    @property
    def ready(self) -> bool:
        return bool(self.host and EMAIL_RE.fullmatch(self.sender) and 0 < self.port < 65536
                    and not (self.use_ssl and self.use_tls)
                    and (not self.user or (self.password and (self.use_tls or self.use_ssl))))


def image_to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def postcard(image: Image.Image, preset_key: str, *, original: Image.Image | None = None) -> Image.Image:
    """Photo/portrait collage with bilingual labels; compose exclusively in RAM.

    The app passes the same original square crop used to compute the portrait.
    Omitting it preserves the single-image format for existing callers.
    """
    panel = 900
    padding = 24
    if original is not None:
        header = 64
        width = panel*2 + padding*3
        bottom = header + panel
        card = Image.new("RGB", (width, bottom+180), "#f4f7fb")
        for source, x in ((original, padding), (image, panel+padding*2)):
            tile = ImageOps.fit(source.convert("RGB"), (panel, panel), method=Image.Resampling.LANCZOS)
            card.paste(tile, (x, header))
    else:
        width, bottom = panel, panel
        card = Image.new("RGB", (width, bottom+180), "#f4f7fb")
        im = image.copy().convert("RGB")
        im.thumbnail((panel, panel), Image.Resampling.LANCZOS)
        card.paste(im, ((panel-im.width)//2, (panel-im.height)//2))
    draw = ImageDraw.Draw(card)
    font = _postcard_font(23)
    if original is not None:
        for key, x in (("postcard_original", padding), ("postcard_portrait", panel+padding*2)):
            draw.text((x, 19), tr("fr", key)+" / "+tr("en", key), font=font, fill="#12223a")
    title = tr("fr", preset_key+"_name") + " / " + tr("en", preset_key+"_name")
    draw.text((padding, bottom+22), title, font=font, fill="#12223a")
    for y, line in zip((bottom+63, bottom+94, bottom+133), (
        tr("fr", "postcard_rule"), tr("en", "postcard_rule"),
        tr("fr", "postcard_model")+" / "+tr("en", "postcard_model")+" · "+tr("fr", "turing")+", 1952")):
        draw.text((padding, y), line, font=_postcard_font(20), fill="#12223a")
    return card


def send_pattern_email(recipient: str, image: Image.Image, config: SMTPConfig,
                       preset_key: str = "labyrinth", cancel: threading.Event | None = None,
                       *, original: Image.Image | None = None) -> None:
    """Attach from RAM. Reset cancels before transport; sent mail cannot be recalled."""
    if not config.ready:
        raise ValueError("SMTP configuration unavailable")
    if not EMAIL_RE.fullmatch(recipient.strip()):
        raise ValueError("Invalid recipient")
    if cancel and cancel.is_set():
        return
    msg = EmailMessage()
    msg["Subject"] = tr("fr", "mail_subject")+" / "+tr("en", "mail_subject")+" · Turing"
    msg["From"] = config.sender
    msg["To"] = recipient.strip()
    msg.set_content("\n".join(
        tr(lang, "mail_body", model=tr(lang, PRESETS[preset_key].model))
        + (tr(lang, "mail_collage") if original is not None else "")
        for lang in ("fr", "en")))
    msg.add_attachment(image_to_png_bytes(postcard(image, preset_key, original=original)),
                       maintype="image", subtype="png",
                       filename="turing_collage.png" if original is not None else "turing_portrait.png")
    context = ssl.create_default_context()
    if cancel and cancel.is_set():
        return
    transport = smtplib.SMTP_SSL(config.host, config.port, context=context, timeout=20) if config.use_ssl else smtplib.SMTP(config.host, config.port, timeout=20)
    with transport as smtp:
        if not config.use_ssl:
            smtp.ehlo()
            if config.use_tls:
                smtp.starttls(context=context)
                smtp.ehlo()
        if config.user:
            smtp.login(config.user, config.password)
        if not cancel or not cancel.is_set():
            smtp.send_message(msg)


@dataclass
class Result:
    revision: int
    original: Image.Image
    preset: str
    frames: list[Image.Image]
    fields: list[np.ndarray]
    steps: list[int]
    email_image: Image.Image
    comparison: list[Image.Image] | None
    comparison_preset: str | None
    diffusion_frames: list[Image.Image]
    raw_frames: list[Image.Image]


@dataclass
class Request:
    revision: int
    image: Image.Image
    preset: str
    seed: int
    size: float
    intensity: float
    palette: str
    resolution: int
    compare: bool = False


@dataclass
class VisitorSession:
    """Explicit privacy boundary. UI-owned image references must be cleared too."""
    photo: Optional[Image.Image] = None
    result: Optional[Result] = None
    email: str = ""
    generation: int = 0

    def clear(self) -> None:
        self.photo = None
        self.result = None
        self.email = ""
        self.generation += 1


class ModelWorker:
    """One active job, one replaceable pending job, one result mailbox.

    Revisions cancel running work and reject stale completions. Only the GUI
    polls the mailbox; workers never call Tk (including root.after).
    """
    def __init__(self, thumbnails: bool = True) -> None:
        self.condition = threading.Condition()
        self.revision = 0
        self.pending: Request | None = None
        self.result: Result | None = None
        self.error: int | None = None
        self.progress = 0.
        self.closed = False
        self.thumbnails: dict[str, Image.Image] = {}
        self.thread = threading.Thread(target=self._run, args=(thumbnails,), daemon=True, name="pattern-worker")
        self.thread.start()

    def submit(self, **kwargs) -> int:
        with self.condition:
            self.revision += 1
            self.pending = Request(revision=self.revision, **kwargs)
            self.result = None
            self.error = None
            self.progress = 0.
            self.condition.notify()
            return self.revision

    def cancel(self) -> None:
        with self.condition:
            self.revision += 1
            self.pending = self.result = self.error = None
            self.progress = 0.
            self.condition.notify()

    def take(self) -> tuple[Result | None, int | None, float, dict[str, Image.Image]]:
        with self.condition:
            out = self.result, self.error, self.progress, self.thumbnails.copy()
            self.result = self.error = None
            return out

    def _cancelled(self, revision: int) -> bool:
        with self.condition:
            return self.closed or revision != self.revision

    def _progress(self, revision: int, value: float) -> None:
        with self.condition:
            if revision == self.revision:
                self.progress = value

    def _run(self, thumbnails: bool) -> None:
        if thumbnails:
            gray = prepare_gray(demo_image(), 80)
            for key, preset in PRESETS.items():
                if self.closed:
                    return
                try:
                    _, fields = evolve(gray, preset, seed=19, snapshots=2, cancel=lambda: self.closed)
                except Cancelled:
                    return
                with self.condition:
                    self.thumbnails[key] = colour_field(fields[-1], preset)
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                request = self.pending
                self.pending = None
            try:
                result = self._compute(request)
                with self.condition:
                    if request.revision == self.revision and not self.closed:
                        self.result = result
            except Cancelled:
                pass
            except Exception:
                # Exceptions may contain private values: expose only a revision.
                with self.condition:
                    if request.revision == self.revision:
                        self.error = request.revision
            finally:
                request = result = None  # release images when the thread is idle

    def _compute(self, q: Request) -> Result:
        cancel = lambda: self._cancelled(q.revision)
        w, h = q.image.size
        side = min(w, h)
        q.image = q.image.crop(((w-side)//2, (h-side)//2, (w+side)//2, (h+side)//2))
        p = PRESETS[q.preset]
        gray = prepare_gray(q.image, q.resolution)
        steps, fields = evolve(gray, p, q.seed, q.size, cancel=cancel,
                               progress=lambda f: self._progress(q.revision, f*(.5 if q.compare else .85)))
        frames = []
        for field in fields:
            if cancel():
                raise Cancelled()
            frames.append(portrait(q.image, field, p, q.intensity, q.palette, q.resolution))
        comparison = None
        other = None
        if q.compare:
            keys = list(PRESETS)
            other = keys[(keys.index(q.preset)+1) % len(keys)]
            _, other_fields = evolve(gray, PRESETS[other], q.seed, q.size, cancel=cancel,
                                     progress=lambda f: self._progress(q.revision, .5+.4*f))
            comparison = [portrait(q.image, f, PRESETS[other], q.intensity, None, q.resolution) for f in other_fields]
        if cancel():
            raise Cancelled()
        highres = portrait(q.image, fields[-1], p, q.intensity, q.palette, 900)
        from turing_models import diffusion
        diff = [Image.fromarray(np.uint8(np.clip(diffusion(gray, i/(len(fields)-1)*180), 0, 1)*255)).convert("RGB") for i in range(len(fields))]
        raw = [colour_field(f, p, "mono") for f in fields]
        self._progress(q.revision, 1.)
        return Result(q.revision, q.image, q.preset, frames, fields, steps, highres, comparison, other, diff, raw)

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.pending = self.result = None
            self.condition.notify()
        self.thread.join(timeout=1.)


class CameraWorker:
    """Camera open/read/release all belong to one thread; only one frame is held."""
    def __init__(self, index: int) -> None:
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.frame: Image.Image | None = None
        self.available = False
        self.thread = threading.Thread(target=self._run, args=(index,), daemon=True, name="camera-worker")
        self.thread.start()

    def _run(self, index: int) -> None:
        cap = None
        try:
            cap = cv2.VideoCapture(index)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            while not self.stop.is_set() and cap.isOpened():
                ok, frame = cap.read()
                with self.lock:
                    self.available = bool(ok)
                    self.frame = Image.fromarray(cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)) if ok else None
                if not ok:
                    break
                self.stop.wait(.03)
        except Exception:
            # Camera failures should leave the generated demo available.
            pass
        finally:
            if cap is not None:
                cap.release()
            with self.lock:
                self.frame = None
                self.available = False

    def latest(self) -> Image.Image | None:
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def clear(self) -> None:
        with self.lock:
            self.frame = None

    def close(self) -> None:
        self.stop.set()
        self.clear()
        self.thread.join(timeout=1.)
