#!/usr/bin/env python3
"""
Turing Pattern Portrait
=======================

Kiosk-style science-fair application:

    webcam -> photo -> one-field Turing-style pattern -> email

The pattern model is a reduced nonlocal activator/inhibitor equation

    du/dt = R*u - C*u^3
            + Gamma * (G_sigma_a * u - G_sigma_i * u)

with sigma_i > sigma_a.

The difference of Gaussian convolutions produces short-range activation and
longer-range inhibition, selecting a finite spatial scale.  This is a reduced
pattern-forming model rather than a literal two-species Turing chemistry.

INSTALL
-------
Python packages:

    pip install numpy scipy opencv-python pillow

On Ubuntu/Debian, Tk may also need:

    sudo apt install python3-tk

EMAIL SETUP
-----------
The app deliberately does NOT contain credentials. Set these environment
variables before launching it:

    export SMTP_HOST="smtp.gmail.com"
    export SMTP_PORT="587"
    export SMTP_USER="your_lab_account@gmail.com"
    export SMTP_PASSWORD="YOUR_APP_PASSWORD"
    export SMTP_FROM="your_lab_account@gmail.com"
    export SMTP_USE_TLS="true"
    export SMTP_USE_SSL="false"

For Gmail, use an App Password rather than the normal account password.
Other SMTP providers work too; change HOST/PORT/TLS/SSL accordingly.

Optional:

    export CAMERA_INDEX="0"

RUN
---
    python turing_fair_app.py

Keyboard:
    F11  toggle fullscreen
    Esc  leave fullscreen

PRIVACY
-------
This program does not intentionally save captured photos or visitor email
addresses to disk. Images are held in memory and attached directly to email.
The email field is cleared after a successful send. Your SMTP/email provider
will, of course, process the outgoing message.
"""

from __future__ import annotations

import io
import os
import re
import ssl
import smtplib
import threading
from email.message import EmailMessage
from typing import Optional

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter
from PIL import Image, ImageTk

import tkinter as tk
from tkinter import ttk, messagebox


# ---------------------------------------------------------------------------
# Pattern model
# ---------------------------------------------------------------------------

R = -0.15
CUBIC = 1.0
DT = 0.20
N_STEPS = 60

DEFAULT_PATTERN_SIZE = 1.2
DEFAULT_PATTERN_STRENGTH = 1.2
INHIBITION_RATIO = 4.0
PROCESSING_RESOLUTION = 320
EMAIL_IMAGE_SIZE = 900


def center_square(frame_bgr: np.ndarray) -> np.ndarray:
    """Return the largest centered square crop."""
    h, w = frame_bgr.shape[:2]
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    return frame_bgr[y0:y0 + side, x0:x0 + side]


def make_turing_pattern(
    frame_bgr: np.ndarray,
    pattern_size: float = DEFAULT_PATTERN_SIZE,
    strength: float = DEFAULT_PATTERN_STRENGTH,
    high_contrast: bool = True,
    resolution: int = PROCESSING_RESOLUTION,
    n_steps: int = N_STEPS,
) -> tuple[Image.Image, Image.Image]:
    """
    Transform a webcam frame into a Turing-style pattern.

    Returns:
        original_pil: centered square RGB photo
        pattern_pil:  transformed RGB image
    """
    square = center_square(frame_bgr)

    # Keep an RGB copy for the GUI.
    original_rgb = cv2.cvtColor(square, cv2.COLOR_BGR2RGB)
    original_pil = Image.fromarray(original_rgb)

    # Work at modest resolution for fast interactive use.
    gray = cv2.cvtColor(square, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray,
        (resolution, resolution),
        interpolation=cv2.INTER_AREA,
    ).astype(np.float32) / 255.0

    # Initial field: photograph supplies the perturbation.
    u = gray - float(gray.mean())
    std = float(u.std())
    if std > 1e-6:
        u /= std
    u *= 0.15

    sigma_a = max(0.35, float(pattern_size))
    sigma_i = INHIBITION_RATIO * sigma_a
    gamma = float(strength)

    # Explicit Euler. Gaussian filtering is implemented in optimized SciPy C.
    for _ in range(int(n_steps)):
        short_range = gaussian_filter(u, sigma=sigma_a, mode="reflect")
        long_range = gaussian_filter(u, sigma=sigma_i, mode="reflect")

        du = (
            R * u
            - CUBIC * u * u * u
            + gamma * (short_range - long_range)
        )
        u += DT * du

    # Robust normalization.
    scale = float(np.percentile(np.abs(u), 99.5))
    if scale < 1e-8:
        scale = 1.0
    z = np.clip(u / scale, -1.0, 1.0)

    if high_contrast:
        # Strong black/white print-like rendering.
        out = (z > 0.0).astype(np.uint8) * 255
    else:
        # Smooth high-contrast grayscale rendering.
        out = ((0.5 + 0.5 * np.tanh(2.0 * z)) * 255.0).astype(np.uint8)

    pattern_pil = Image.fromarray(out, mode="L").convert("RGB")
    return original_pil, pattern_pil


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class SMTPConfig:
    def __init__(self) -> None:
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.user = os.getenv("SMTP_USER", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.sender = os.getenv("SMTP_FROM", self.user).strip()
        self.use_tls = env_bool("SMTP_USE_TLS", True)
        self.use_ssl = env_bool("SMTP_USE_SSL", False)

    @property
    def ready(self) -> bool:
        return bool(self.host and self.sender)


def image_to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def send_pattern_email(
    recipient: str,
    image: Image.Image,
    config: SMTPConfig,
) -> None:
    """Send image directly from memory; no temporary file is created."""
    if not config.ready:
        raise RuntimeError(
            "Email is not configured. Set SMTP_HOST and SMTP_FROM "
            "(plus credentials if your SMTP server requires them)."
        )

    recipient = recipient.strip()
    if not EMAIL_RE.match(recipient):
        raise ValueError("Please enter a valid email address.")

    # Send a reasonably large image while keeping attachment size modest.
    image = image.copy()
    image.thumbnail((EMAIL_IMAGE_SIZE, EMAIL_IMAGE_SIZE), Image.Resampling.LANCZOS)
    png = image_to_png_bytes(image)

    msg = EmailMessage()
    msg["Subject"] = "Your Turing-pattern portrait"
    msg["From"] = config.sender
    msg["To"] = recipient
    msg.set_content(
        "Thanks for visiting our science-festival stand!\n\n"
        "Attached is your Turing-pattern portrait, generated from a "
        "pattern-forming activator/inhibitor model.\n\n"
        "The fair application does not save your photo or email address locally."
    )
    msg.add_attachment(
        png,
        maintype="image",
        subtype="png",
        filename="turing_portrait.png",
    )

    context = ssl.create_default_context()

    if config.use_ssl:
        with smtplib.SMTP_SSL(
            config.host,
            config.port,
            context=context,
            timeout=20,
        ) as smtp:
            if config.user:
                smtp.login(config.user, config.password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(config.host, config.port, timeout=20) as smtp:
            smtp.ehlo()
            if config.use_tls:
                smtp.starttls(context=context)
                smtp.ehlo()
            if config.user:
                smtp.login(config.user, config.password)
            smtp.send_message(msg)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class TuringFairApp:
    PREVIEW_SIZE = 500

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Turing Pattern Portrait")
        self.root.minsize(1080, 720)

        self.fullscreen = False
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.leave_fullscreen)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.smtp = SMTPConfig()

        self.camera_index = int(os.getenv("CAMERA_INDEX", "0"))
        self.cap = cv2.VideoCapture(self.camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.latest_frame: Optional[np.ndarray] = None
        self.captured_frame: Optional[np.ndarray] = None
        self.original_image: Optional[Image.Image] = None
        self.pattern_image: Optional[Image.Image] = None

        self._live_tk = None
        self._original_tk = None
        self._pattern_tk = None

        self.build_ui()
        self.update_camera()

        if not self.cap.isOpened():
            self.status_var.set(
                f"Could not open webcam {self.camera_index}. "
                "Check CAMERA_INDEX and camera permissions."
            )

    # ---------- UI construction ----------

    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)

        title = ttk.Label(
            main,
            text="TURN YOUR PHOTO INTO A TURING PATTERN",
            font=("TkDefaultFont", 20, "bold"),
            anchor="center",
        )
        title.pack(fill="x", pady=(0, 4))

        subtitle = ttk.Label(
            main,
            text=(
                "Short-range activation + longer-range inhibition "
                "selects a spatial pattern."
            ),
            anchor="center",
        )
        subtitle.pack(fill="x", pady=(0, 12))

        # Image area
        images = ttk.Frame(main)
        images.pack(fill="both", expand=True)
        images.columnconfigure(0, weight=1)
        images.columnconfigure(1, weight=1)
        images.rowconfigure(1, weight=1)

        ttk.Label(
            images,
            text="CAMERA / ORIGINAL",
            font=("TkDefaultFont", 12, "bold"),
        ).grid(row=0, column=0, pady=(0, 6))

        ttk.Label(
            images,
            text="TURING PATTERN",
            font=("TkDefaultFont", 12, "bold"),
        ).grid(row=0, column=1, pady=(0, 6))

        self.left_image = ttk.Label(images, anchor="center")
        self.left_image.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        self.right_image = ttk.Label(
            images,
            text="Take a photo to create a pattern.",
            anchor="center",
        )
        self.right_image.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        # Controls
        controls = ttk.Frame(main, padding=(0, 12, 0, 0))
        controls.pack(fill="x")
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)

        ttk.Label(controls, text="Pattern size").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.size_var = tk.DoubleVar(value=DEFAULT_PATTERN_SIZE)
        self.size_scale = ttk.Scale(
            controls,
            from_=0.7,
            to=3.5,
            variable=self.size_var,
            orient="horizontal",
        )
        self.size_scale.grid(row=0, column=1, sticky="ew", padx=(0, 20))

        ttk.Label(controls, text="Pattern strength").grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.strength_var = tk.DoubleVar(value=DEFAULT_PATTERN_STRENGTH)
        self.strength_scale = ttk.Scale(
            controls,
            from_=0.6,
            to=1.8,
            variable=self.strength_var,
            orient="horizontal",
        )
        self.strength_scale.grid(row=0, column=3, sticky="ew")

        self.high_contrast_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            controls,
            text="Black & white",
            variable=self.high_contrast_var,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(controls)
        buttons.grid(row=1, column=1, columnspan=3, sticky="e", pady=(8, 0))

        self.capture_button = ttk.Button(
            buttons,
            text="TAKE PHOTO + CREATE PATTERN",
            command=self.capture_and_transform,
        )
        self.capture_button.pack(side="left", padx=4)

        self.update_button = ttk.Button(
            buttons,
            text="UPDATE PATTERN",
            command=self.reprocess,
            state="disabled",
        )
        self.update_button.pack(side="left", padx=4)

        self.new_button = ttk.Button(
            buttons,
            text="NEW PHOTO",
            command=self.new_photo,
        )
        self.new_button.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(
            main,
            mode="indeterminate",
            length=300,
        )
        self.progress.pack(fill="x", pady=(10, 4))

        # Email area
        email_frame = ttk.LabelFrame(main, text="Receive your portrait by email", padding=10)
        email_frame.pack(fill="x", pady=(8, 0))
        email_frame.columnconfigure(1, weight=1)

        ttk.Label(email_frame, text="Email").grid(
            row=0, column=0, padx=(0, 8), sticky="w"
        )

        self.email_var = tk.StringVar()
        self.email_entry = ttk.Entry(
            email_frame,
            textvariable=self.email_var,
            font=("TkDefaultFont", 12),
        )
        self.email_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.email_entry.bind("<Return>", lambda _event: self.send_email())

        self.send_button = ttk.Button(
            email_frame,
            text="SEND PHOTO",
            command=self.send_email,
            state="disabled",
        )
        self.send_button.grid(row=0, column=2)

        privacy = ttk.Label(
            email_frame,
            text=(
                "This app does not save your photo or email address locally. "
                "The address is used only to send this image."
            ),
        )
        privacy.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.status_var = tk.StringVar()
        if self.smtp.ready:
            self.status_var.set("Ready. Look at the camera and take a photo.")
        else:
            self.status_var.set(
                "Camera ready. Email sending is disabled until SMTP is configured."
            )

        status = ttk.Label(
            main,
            textvariable=self.status_var,
            anchor="center",
        )
        status.pack(fill="x", pady=(8, 0))

    # ---------- Webcam ----------

    def update_camera(self) -> None:
        if self.cap.isOpened():
            ok, frame = self.cap.read()
            if ok:
                # Mirror view: more natural for visitors.
                frame = cv2.flip(frame, 1)
                self.latest_frame = frame

                # Only show live camera if a captured photo is not currently shown.
                if self.captured_frame is None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil = Image.fromarray(rgb)
                    self.show_pil_on_label(
                        pil,
                        self.left_image,
                        "_live_tk",
                        self.PREVIEW_SIZE,
                    )

        self.root.after(30, self.update_camera)

    # ---------- Image processing ----------

    def capture_and_transform(self) -> None:
        if self.latest_frame is None:
            messagebox.showerror("Camera", "No webcam frame is available.")
            return

        self.captured_frame = self.latest_frame.copy()
        self.start_transform()

    def reprocess(self) -> None:
        if self.captured_frame is not None:
            self.start_transform()

    def start_transform(self) -> None:
        if self.captured_frame is None:
            return

        self.capture_button.configure(state="disabled")
        self.update_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("Creating the pattern...")

        frame = self.captured_frame.copy()
        size = float(self.size_var.get())
        strength = float(self.strength_var.get())
        high_contrast = bool(self.high_contrast_var.get())

        worker = threading.Thread(
            target=self._transform_worker,
            args=(frame, size, strength, high_contrast),
            daemon=True,
        )
        worker.start()

    def _transform_worker(
        self,
        frame: np.ndarray,
        size: float,
        strength: float,
        high_contrast: bool,
    ) -> None:
        try:
            original, pattern = make_turing_pattern(
                frame,
                pattern_size=size,
                strength=strength,
                high_contrast=high_contrast,
            )
            self.root.after(
                0,
                lambda: self._finish_transform(original, pattern),
            )
        except Exception as exc:
            self.root.after(0, lambda: self._transform_error(exc))

    def _finish_transform(
        self,
        original: Image.Image,
        pattern: Image.Image,
    ) -> None:
        self.original_image = original
        self.pattern_image = pattern

        self.show_pil_on_label(
            original,
            self.left_image,
            "_original_tk",
            self.PREVIEW_SIZE,
        )
        self.show_pil_on_label(
            pattern,
            self.right_image,
            "_pattern_tk",
            self.PREVIEW_SIZE,
        )

        self.progress.stop()
        self.capture_button.configure(state="normal")
        self.update_button.configure(state="normal")
        self.send_button.configure(state="normal")
        self.status_var.set(
            "Pattern ready. Adjust the controls and press UPDATE PATTERN, "
            "or enter an email address."
        )

    def _transform_error(self, exc: Exception) -> None:
        self.progress.stop()
        self.capture_button.configure(state="normal")
        self.update_button.configure(state="normal")
        self.status_var.set("Pattern generation failed.")
        messagebox.showerror("Pattern error", str(exc))

    def new_photo(self) -> None:
        self.captured_frame = None
        self.original_image = None
        self.pattern_image = None
        self.email_var.set("")
        self.right_image.configure(image="", text="Take a photo to create a pattern.")
        self._pattern_tk = None
        self._original_tk = None
        self.update_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.status_var.set("Ready for a new photo.")

    # ---------- Email ----------

    def send_email(self) -> None:
        recipient = self.email_var.get().strip()

        if self.pattern_image is None:
            messagebox.showinfo("Photo", "Create a pattern first.")
            return

        if not EMAIL_RE.match(recipient):
            messagebox.showerror("Email", "Please enter a valid email address.")
            self.email_entry.focus_set()
            return

        if not self.smtp.ready:
            messagebox.showerror(
                "Email not configured",
                "SMTP is not configured on this computer.\n\n"
                "Set SMTP_HOST, SMTP_PORT, SMTP_FROM and, when needed, "
                "SMTP_USER / SMTP_PASSWORD before starting the app.",
            )
            return

        self.send_button.configure(state="disabled")
        self.status_var.set("Sending email...")

        # Copy so the visitor can already interact with the GUI safely.
        image = self.pattern_image.copy()

        worker = threading.Thread(
            target=self._email_worker,
            args=(recipient, image),
            daemon=True,
        )
        worker.start()

    def _email_worker(self, recipient: str, image: Image.Image) -> None:
        try:
            send_pattern_email(recipient, image, self.smtp)
            self.root.after(0, self._email_success)
        except Exception as exc:
            self.root.after(0, lambda: self._email_error(exc))

    def _email_success(self) -> None:
        # Do not keep visitor email address in the interface.
        self.email_var.set("")
        self.send_button.configure(state="normal")
        self.status_var.set("Email sent. You can now take a new photo.")
        messagebox.showinfo("Sent", "Your Turing-pattern portrait was sent!")

    def _email_error(self, exc: Exception) -> None:
        self.send_button.configure(state="normal")
        self.status_var.set("Could not send the email.")
        messagebox.showerror(
            "Email error",
            f"Could not send the photo:\n\n{exc}",
        )

    # ---------- Display helpers ----------

    def show_pil_on_label(
        self,
        image: Image.Image,
        label: ttk.Label,
        attr_name: str,
        max_size: int,
    ) -> None:
        img = image.copy()
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        tk_img = ImageTk.PhotoImage(img)
        setattr(self, attr_name, tk_img)
        label.configure(image=tk_img, text="")

    # ---------- Window ----------

    def toggle_fullscreen(self, _event=None) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def leave_fullscreen(self, _event=None) -> None:
        self.fullscreen = False
        self.root.attributes("-fullscreen", False)

    def close(self) -> None:
        if self.cap.isOpened():
            self.cap.release()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = TuringFairApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
