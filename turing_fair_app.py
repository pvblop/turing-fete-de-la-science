#!/usr/bin/env python3
"""Offline bilingual Tk kiosk: python turing_fair_app.py --demo.

Tk is used only on the main thread. Camera, model and SMTP work use bounded
mailboxes; photographs and visitor addresses are never written to disk.
"""
from __future__ import annotations
import argparse
import os
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageOps, ImageTk
from turing_i18n import tr
from turing_models import PRESETS, PALETTES, demo_image, parameters
from turing_services import CameraWorker, EMAIL_RE, ModelWorker, SMTPConfig, VisitorSession, send_pattern_email

BG, INK, TEAL = "#f2f6fa", "#142640", "#087f86"


class TuringFairApp:
    """Six short stages with optional email after the educational journey."""
    def __init__(self, root: tk.Tk, *, demo: bool = False, language: str = "fr",
                 fullscreen: bool = False, camera_index: int = 0,
                 resolution: int = 224, seed: int = 19) -> None:
        self.root, self.language, self.resolution, self.seed = root, language, resolution, seed
        self.stage, self.closed, self.fullscreen = 0, False, fullscreen
        self.session, self.model = VisitorSession(), ModelWorker()
        self.camera = None if demo else CameraWorker(camera_index)
        self.smtp = SMTPConfig()
        self.revision, self.busy, self.frame_index = 0, False, 0
        self.playing, self.last_tick = True, 0.
        self.view, self.preset, self.palette, self.speed = "portrait", "labyrinth", "ocean", "normal"
        self.size, self.intensity = tk.DoubleVar(value=1.), tk.DoubleVar(value=.58)
        self.debounce = None
        self.redraw_id = None
        self.photo_refs, self.thumb_refs, self.thumb_buttons, self.thumbs = [], {}, {}, {}
        self.email_var = tk.StringVar()
        self.email_window, self.email_busy = None, False
        self.email_cancel, self.email_results = threading.Event(), queue.Queue(maxsize=1)
        self.status_key, self.status = "ready", tk.StringVar()
        self.root.title(self.t("app"))
        self.root.geometry("1366x768")
        self.root.minsize(1000, 680)
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", fullscreen)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.leave_fullscreen)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("TkDefaultFont", 13))
        style.configure("Title.TLabel", font=("TkDefaultFont", 26, "bold"))
        style.configure("Note.TLabel", foreground="#39536b", font=("TkDefaultFont", 12))
        style.configure("TButton", font=("TkDefaultFont", 12, "bold"), padding=(10, 9))
        style.configure("Primary.TButton", background=TEAL, foreground="white")
        style.map("Primary.TButton", background=[("active", "#05676c")], foreground=[("active", "white")])
        style.configure("Thumb.TButton", font=("TkDefaultFont", 11, "bold"), padding=(5, 5))
        style.configure("Selected.Thumb.TButton", background=TEAL, foreground="white")
        style.configure("TCombobox", padding=5)
        self.root.option_add("*TCombobox*Listbox.font", ("TkDefaultFont", 14))
        self.shell = ttk.Frame(root, padding=(20, 12))
        self.shell.pack(fill="both", expand=True)
        header = ttk.Frame(self.shell)
        header.pack(fill="x")
        self.brand = ttk.Label(header, foreground=TEAL, font=("TkDefaultFont", 12, "bold"))
        self.brand.pack(side="left")
        self.lang_button = ttk.Button(header, command=self.switch_language)
        self.lang_button.pack(side="right")
        self.step_label = ttk.Label(header)
        self.step_label.pack(side="right", padx=20)
        self.title = ttk.Label(self.shell, style="Title.TLabel")
        self.title.pack(fill="x", pady=(8, 3))
        self.intro = ttk.Label(self.shell, wraplength=1250)
        self.intro.pack(fill="x", pady=(0, 8))
        # Footer is packed before content to keep navigation visible.
        footer = ttk.Frame(self.shell)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        self.back_button = ttk.Button(footer, command=lambda: self.go(-1))
        self.back_button.pack(side="left")
        self.reset_button = ttk.Button(footer, command=self.reset)
        self.reset_button.pack(side="left", padx=8)
        self.next_button = ttk.Button(footer, style="Primary.TButton", command=lambda: self.go(1))
        self.next_button.pack(side="right")
        ttk.Label(footer, textvariable=self.status, style="Note.TLabel", wraplength=540).pack(side="left", padx=12, fill="x", expand=True)
        self.body = ttk.Frame(self.shell)
        self.body.pack(fill="both", expand=True)
        self.build_stage()
        self.root.bind("<Configure>", self.on_resize, add="+")
        self.poll_id = root.after(50, self.poll)

    def t(self, key: str, **values) -> str:
        return tr(self.language, key, **values)

    def button(self, parent, key: str, command, **kwargs) -> ttk.Button:
        return ttk.Button(parent, text=self.t(key), command=command, **kwargs)

    def build_stage(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.photo_refs.clear()
        self.thumb_buttons.clear()
        self.brand.configure(text=self.t("festival"))
        self.lang_button.configure(text=self.t("language"))
        self.step_label.configure(text=self.t("step", n=self.stage+1))
        self.title.configure(text=self.t(f"stage{self.stage}_title"))
        self.intro.configure(text=self.t(f"stage{self.stage}_text"))
        self.back_button.configure(text=self.t("back"), state="normal" if self.stage else "disabled")
        self.reset_button.configure(text=self.t("reset"))
        self.next_button.configure(text=self.t("start" if self.stage == 3 else "next"), state="disabled" if self.stage == 5 else "normal")
        self.status.set(self.t(self.status_key))
        if self.stage < 3:
            self.canvas = tk.Canvas(self.body, bg="white", highlightthickness=0)
            self.canvas.pack(fill="both", expand=True)
            self.canvas.bind("<Configure>", lambda _e: self.draw_story())
            if self.stage == 2:
                self.range_var = tk.DoubleVar(value=1.)
                row = ttk.Frame(self.body)
                row.pack(fill="x", pady=8)
                ttk.Label(row, text=self.t("range")).pack(side="left")
                ttk.Scale(row, from_=.7, to=1.6, variable=self.range_var, command=lambda _v: self.draw_story()).pack(side="left", fill="x", expand=True, padx=20)
            ttk.Label(self.body, text=self.t(f"stage{self.stage}_note"), wraplength=1250, style="Note.TLabel").pack(fill="x", pady=8)
            self.draw_story()
        elif self.stage < 5:
            self.build_experiment()
        else:
            ttk.Label(self.body, text=self.t("stage5_note"), style="Title.TLabel", wraplength=1150, anchor="center").pack(fill="x", pady=15)
            self.finish_image = tk.Canvas(self.body, bg=BG, highlightthickness=0)
            self.finish_image.pack(fill="both", expand=True)
            self.finish_image.bind("<Configure>", self.schedule_redraw)
            self.button(self.body, "email_optional", self.open_email, style="Primary.TButton").pack(pady=8)
            ttk.Label(self.body, text=self.t("privacy"), wraplength=1250, style="Note.TLabel").pack(fill="x")
            self.display_frame()

    def draw_story(self) -> None:
        if self.stage > 2:
            return
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 600), max(c.winfo_height(), 220)
        self.photo_refs.clear()
        if self.stage == 0:
            for i, key in enumerate(PRESETS):
                x, sz = w*(i+.5)/4, int(min(w/4-30, h-80))
                if key in self.thumbs:
                    photo = ImageTk.PhotoImage(self.thumbs[key].resize((sz, sz)))
                    self.photo_refs.append(photo)
                    c.create_image(x, h/2-18, image=photo)
                else:
                    c.create_oval(x-50, h/2-68, x+50, h/2+32, fill=TEAL, outline="")
                c.create_text(x, h-28, text=self.t(key+"_name"), fill=INK, font=("TkDefaultFont", 18, "bold"))
        elif self.stage == 1:
            c.create_text(w*.28, h*.43, text="1952", fill=TEAL, font=("TkDefaultFont", 70, "bold"))
            c.create_text(w*.28, h*.72, text=self.t("turing"), fill=INK, font=("TkDefaultFont", 25, "bold"))
            for i in range(12):
                x, y = w*.58+(i%4)*w*.075, h*.22+(i//4)*h*.25
                c.create_oval(x-18, y-18, x+18, y+18, fill="#f3bd51" if i%2 else TEAL, outline="")
                if i%4 < 3:
                    c.create_line(x+22, y, x+w*.075-22, y, fill=INK, arrow="last", width=3)
        else:
            self.draw_mechanism(w, h)

    def draw_mechanism(self, w: int, h: int) -> None:
        """A labelled three-step analogy connects interaction range to spacing."""
        c = self.canvas
        column, y = w/3, h*.52
        for i, key in enumerate(("mechanism_seed", "mechanism_competition", "mechanism_spacing")):
            c.create_rectangle(i*column+6, 8, (i+1)*column-6, h-8, fill="#f4f8fb", outline="#d6e3ed")
            c.create_text((i+.5)*column, 22, anchor="n", text=self.t(key), width=column-35,
                          fill=INK, font=("TkDefaultFont", 17, "bold"))
        seed_x, source_x = column/2, column*1.5
        c.create_oval(seed_x-16, y-16, seed_x+16, y+16, fill="#f3bd51", outline="#916012", width=2)
        reach = self.range_var.get()
        radius = min(column*.27, h*.22)*reach/1.3
        c.create_oval(source_x-radius, y-radius, source_x+radius, y+radius,
                      fill="#c8e8f8", outline="#196396", width=3)
        c.create_oval(source_x-30, y-30, source_x+30, y+30,
                      fill="#f3bd51", outline="#916012", width=3)
        c.create_text(source_x, y+radius+22, text=self.t("mechanism_near"), width=column-30,
                      fill="#765007", font=("TkDefaultFont", 14, "bold"))
        c.create_line(source_x, y+radius+8, source_x, y+34, arrow="last", fill="#916012", width=2)
        c.create_text(source_x, h-40, text=self.t("mechanism_far"), width=column-30,
                      fill="#155078", font=("TkDefaultFont", 13, "bold"))
        gap = 48+32*reach
        count = max(2, int((column-60)/gap)+1)
        start = column*2.5-(count-1)*gap/2
        for i in range(count):
            x = start+i*gap
            c.create_oval(x-16, y-16, x+16, y+16, fill="#f3bd51", outline="#916012", width=2)
        c.create_line(start+18, y+42, start+gap-18, y+42, arrow="both", fill=INK, width=2)
        c.create_text(column*2.5, y+76, text=self.t("mechanism_gap"), width=column-30,
                      fill=INK, font=("TkDefaultFont", 14, "bold"))

    def build_experiment(self) -> None:
        if self.stage == 4:
            sidebar = ttk.Frame(self.body)
            sidebar.pack(side="left", fill="y", padx=(0, 14))
            ttk.Label(sidebar, text=self.t("type"), font=("TkDefaultFont", 15, "bold")).pack(anchor="w", pady=(0, 5))
            grid = ttk.Frame(sidebar)
            grid.pack(fill="x")
            for i, key in enumerate(PRESETS):
                b = self.button(grid, key+"_name", lambda k=key: self.choose_preset(k), style="Selected.Thumb.TButton" if key == self.preset else "Thumb.TButton", compound="left", width=14)
                b.grid(row=i//2, column=i%2, sticky="ew", padx=2, pady=2)
                self.thumb_buttons[key] = b
                if key in self.thumb_refs:
                    b.configure(image=self.thumb_refs[key])
            for key, var, lo, hi in (("size", self.size, .6, 1.8), ("intensity", self.intensity, 0., 1.)):
                ttk.Label(sidebar, text=self.t(key)).pack(anchor="w", pady=(8, 0))
                ttk.Scale(sidebar, from_=lo, to=hi, variable=var, command=self.controls_changed).pack(fill="x")
            ttk.Label(sidebar, text=self.t("palette")).pack(anchor="w", pady=(8, 0))
            combo = ttk.Combobox(sidebar, state="readonly", values=[self.t(k) for k in PALETTES], width=23)
            combo.current(list(PALETTES).index(self.palette))
            combo.pack(fill="x", pady=3)
            combo.bind("<<ComboboxSelected>>", lambda _e: self.change_palette(list(PALETTES)[combo.current()]))
            self.button(sidebar, "scientist", self.show_science).pack(fill="x", pady=8)
        area = ttk.Frame(self.body)
        area.pack(side="left", fill="both", expand=True)
        bottom = ttk.Frame(area)
        bottom.pack(side="bottom", fill="x")
        if self.stage == 4:
            for keys in (("capture", "demo", "surprise", "seed"), ("compare", "whatif", "another")):
                row = ttk.Frame(bottom)
                row.pack(fill="x", pady=3)
                commands = {"capture": self.capture, "demo": self.use_demo, "surprise": self.surprise, "seed": self.regenerate,
                            "compare": self.compare_patterns, "whatif": self.what_if, "another": self.another}
                for key in keys:
                    self.button(row, key, commands[key]).pack(side="left", padx=2)
        row = ttk.Frame(bottom)
        row.pack(fill="x", pady=3)
        self.pause_button = self.button(row, "pause" if self.playing else "play", self.pause)
        self.pause_button.pack(side="left", padx=2)
        self.button(row, "replay", self.replay).pack(side="left", padx=2)
        ttk.Label(row, text=self.t("speed")).pack(side="left", padx=(12, 4))
        speeds = ttk.Combobox(row, state="readonly", values=[self.t(k) for k in ("slow", "normal", "fast")], width=9)
        speeds.current(("slow", "normal", "fast").index(self.speed))
        speeds.pack(side="left")
        speeds.bind("<<ComboboxSelected>>", lambda _e: setattr(self, "speed", ("slow", "normal", "fast")[speeds.current()]))
        if self.stage == 3:
            self.button(row, "scientist", self.show_science).pack(side="right")
        self.progressbar = ttk.Progressbar(bottom, maximum=100)
        self.progressbar.pack(fill="x", pady=5)
        note = self.t("stage3_note") if self.stage == 3 else self.t(self.preset+"_name")+" · "+self.t(self.preset+"_desc")+"\n"+self.t("experiment" if PRESETS[self.preset].experiment else "portrait")
        self.explanation = ttk.Label(bottom, text=note, wraplength=900, style="Note.TLabel")
        self.explanation.pack(fill="x", pady=3)
        images = ttk.Frame(area)
        images.pack(fill="both", expand=True)
        images.columnconfigure((0, 1), weight=1, uniform="images")
        images.rowconfigure(1, weight=1)
        self.left_title = ttk.Label(images, anchor="center", font=("TkDefaultFont", 15, "bold"))
        self.right_title = ttk.Label(images, anchor="center", font=("TkDefaultFont", 15, "bold"))
        self.left_title.grid(row=0, column=0, sticky="ew", pady=4)
        self.right_title.grid(row=0, column=1, sticky="ew", pady=4)
        self.left_image = tk.Canvas(images, bg="white", highlightthickness=0, width=260, height=220)
        self.right_image = tk.Canvas(images, bg="white", highlightthickness=0, width=260, height=220)
        self.left_image.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.right_image.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        for canvas in (self.left_image, self.right_image):
            canvas.bind("<Configure>", self.schedule_redraw)
        if self.stage == 3:
            self.view = "diffusion"
            if self.session.photo is None:
                self.session.photo = demo_image()
            if self.preset != "labyrinth":
                self.preset, self.palette = "labyrinth", "ocean"
                self.controls_changed()
            elif self.session.result is None and not self.busy:
                self.request_transform()
        elif self.view == "diffusion":
            self.view = "portrait"
        self.display_frame()

    def choose_preset(self, key: str) -> None:
        self.preset, self.palette = key, PRESETS[key].palette
        self.view = "portrait"
        self.controls_changed()
        self.build_stage()

    def change_palette(self, key: str) -> None:
        self.palette = key
        self.controls_changed()

    def controls_changed(self, _value=None) -> None:
        if self.closed:
            return
        if self.debounce:
            self.root.after_cancel(self.debounce)
        self.model.cancel()
        self.session.result = None
        self.busy = True
        self.debounce = self.root.after(240, self.request_transform)

    def request_transform(self) -> None:
        self.debounce = None
        if self.closed or self.session.photo is None:
            self.busy = False
            return
        self.busy, self.session.result, self.frame_index = True, None, 0
        self.revision = self.model.submit(image=self.session.photo.copy(), preset=self.preset,
            seed=self.seed, size=self.size.get(), intensity=self.intensity.get(),
            palette=self.palette, resolution=self.resolution, compare=self.view == "compare")

    def capture(self) -> None:
        image = self.camera.latest() if self.camera else None
        if image is None:
            self.status_key = "camera_missing"
            self.session.photo = demo_image()
        else:
            self.status_key, self.session.photo = "camera_ready", image
        self.view = "portrait"
        self.controls_changed()

    def use_demo(self) -> None:
        self.session.photo = demo_image()
        self.controls_changed()

    def regenerate(self) -> None:
        self.seed = random.SystemRandom().randrange(2**31)
        self.controls_changed()

    def surprise(self) -> None:
        self.seed = random.SystemRandom().randrange(2**31)
        self.choose_preset(random.choice(list(PRESETS)))

    def another(self) -> None:
        keys = list(PRESETS)
        self.choose_preset(keys[(keys.index(self.preset)+1)%len(keys)])

    def what_if(self) -> None:
        self.size.set(1.65 if self.size.get() < 1.4 else .75)
        self.controls_changed()

    def compare_patterns(self) -> None:
        self.view = "compare" if self.view != "compare" else "portrait"
        self.controls_changed()

    def pause(self) -> None:
        self.playing = not self.playing
        self.pause_button.configure(text=self.t("pause" if self.playing else "play"))

    def replay(self) -> None:
        self.frame_index, self.playing = 0, True
        self.pause_button.configure(text=self.t("pause"))
        self.display_frame()

    def show_science(self) -> None:
        p = PRESETS[self.preset]
        popup = tk.Toplevel(self.root)
        popup.title(self.t("scientist"))
        popup.configure(bg=BG)
        popup.geometry("760x460")
        for text in (self.t("science_diffusion" if self.stage == 3 else "science_"+p.model),
                     self.t(p.model)+"\n"+self.t("seed_value", seed=self.seed)+"\n"+
                     ", ".join(f"{k}={v:.4g}" for k,v in parameters(p, self.size.get()).items()), self.t("stage4_note")):
            ttk.Label(popup, text=text, wraplength=700, padding=20).pack(fill="x")
        self.button(popup, "back", popup.destroy).pack(pady=10)

    def schedule_redraw(self, _event=None) -> None:
        """Wait for child layout after fullscreen/resizing, including while paused."""
        if self.closed:
            return
        if self.redraw_id:
            self.root.after_cancel(self.redraw_id)
        self.redraw_id = self.root.after_idle(self.redraw)

    def redraw(self) -> None:
        self.redraw_id = None
        self.display_frame()

    def show_image(self, canvas: tk.Canvas, image: Image.Image, side: int | None = None) -> None:
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if min(w, h) < 6:
            return
        side = side if side is not None else min(w, h)-6
        if side < 1:
            return
        # thumbnail() only shrinks. A 224-pixel simulation must also enlarge
        # to the same square viewport as the high-resolution captured photo.
        im = ImageOps.fit(image, (side, side), method=Image.Resampling.LANCZOS, centering=(.5, .5))
        photo = ImageTk.PhotoImage(im)
        self.photo_refs.append(photo)
        canvas.delete("all")
        canvas.create_image(w/2, h/2, image=photo)

    def display_frame(self) -> None:
        if self.stage < 3:
            return
        self.photo_refs.clear()
        result = self.session.result
        if self.stage == 5:
            self.show_image(self.finish_image, result.email_image if result else demo_image())
            return
        side = min(self.left_image.winfo_width(), self.right_image.winfo_width(),
                   self.left_image.winfo_height(), self.right_image.winfo_height())-6
        if result is None:
            original = self.session.photo or (self.camera.latest() if self.camera and self.stage == 4 else None)
            self.show_image(self.left_image, original or demo_image(), side)
            self.right_image.delete("all")
            self.left_title.configure(text=self.t("original"))
            self.right_title.configure(text=self.t("pattern"))
            return
        idx = min(self.frame_index, len(result.frames)-1)
        if self.view == "diffusion":
            left, right = result.diffusion_frames[idx], result.raw_frames[idx]
            left_key, right_key = "diffusion", "interaction"
        elif self.view == "compare" and result.comparison:
            left, right = result.frames[idx], result.comparison[idx]
            left_key, right_key = result.preset+"_name", result.comparison_preset+"_name"
        else:
            left, right = result.original, result.frames[idx]
            left_key, right_key = "original", "pattern"
        self.left_title.configure(text=self.t(left_key))
        self.right_title.configure(text=self.t(right_key))
        self.show_image(self.left_image, left, side)
        self.show_image(self.right_image, right, side)

    def poll(self) -> None:
        if self.closed:
            return
        result, error, progress, thumbs = self.model.take()
        self.thumbs = thumbs
        changed = False
        for key, im in thumbs.items():
            if key not in self.thumb_refs:
                self.thumb_refs[key] = ImageTk.PhotoImage(im.resize((42, 42)))
                changed = True
                if key in self.thumb_buttons:
                    self.thumb_buttons[key].configure(image=self.thumb_refs[key])
        if changed and self.stage == 0:
            self.draw_story()
        if result and result.revision == self.revision:
            self.session.result, self.busy, self.frame_index = result, False, 0
            self.status.set(self.t(self.status_key))
            self.display_frame()
        if error == self.revision:
            self.busy, self.status_key = False, "model_error"
            self.status.set(self.t(self.status_key))
        if self.busy:
            self.status.set(self.t("computing", percent=int(progress*100)))
        if self.stage in (3, 4):
            self.progressbar["value"] = progress*100 if self.busy else (100 if self.session.result else 0)
            now = time.monotonic()
            if self.playing and self.session.result and now-self.last_tick > {"slow":.6, "normal":.24, "fast":.08}[self.speed]:
                self.display_frame()
                self.frame_index = min(self.frame_index+1, len(self.session.result.frames)-1)
                self.last_tick = now
            elif self.session.photo is None and self.stage == 4:
                self.display_frame()
        try:
            generation, success = self.email_results.get_nowait()
            self.email_busy = False
            if generation == self.session.generation:
                self.status_key = "email_sent" if success else "email_error"
                self.status.set(self.t(self.status_key))
        except queue.Empty:
            pass
        self.poll_id = self.root.after(50, self.poll)

    def open_email(self) -> None:
        if not self.session.result:
            self.status.set(self.t("no_result"))
            return
        if self.email_window and self.email_window.winfo_exists():
            self.email_window.lift()
            return
        popup = self.email_window = tk.Toplevel(self.root)
        popup.title(self.t("email_optional"))
        popup.geometry("760x360")
        popup.configure(bg=BG)
        popup.protocol("WM_DELETE_WINDOW", self.close_email)
        ttk.Label(popup, text=self.t("email"), padding=15).pack(anchor="w")
        entry = ttk.Entry(popup, textvariable=self.email_var, font=("TkDefaultFont", 20))
        entry.pack(fill="x", padx=20)
        entry.bind("<Return>", lambda _e: self.send_email())
        self.button(popup, "send", self.send_email, state="normal" if self.smtp.ready else "disabled", style="Primary.TButton").pack(pady=15)
        if not self.smtp.ready:
            ttk.Label(popup, text=self.t("email_unavailable")).pack()
        for lang in ("fr", "en"):
            ttk.Label(popup, text=tr(lang, "privacy"), wraplength=710, style="Note.TLabel").pack(fill="x", padx=20, pady=4)
        entry.focus_set()

    def close_email(self) -> None:
        self.email_var.set("")
        self.session.email = ""
        if self.email_window:
            self.email_window.destroy()
            self.email_window = None

    def send_email(self) -> None:
        if self.email_busy:
            self.status.set(self.t("email_busy"))
            return
        recipient = self.email_var.get().strip()
        if not EMAIL_RE.fullmatch(recipient):
            self.status.set(self.t("email_invalid"))
            return
        if not self.smtp.ready or not self.session.result:
            self.status.set(self.t("email_unavailable" if not self.smtp.ready else "no_result"))
            return
        result = self.session.result
        image, key, generation = result.email_image.copy(), result.preset, self.session.generation
        original = result.original.copy()
        self.email_cancel = threading.Event()
        cancel = self.email_cancel
        self.email_busy = True
        self.close_email()
        self.status.set(self.t("email_sending"))
        def worker() -> None:
            success = False
            try:
                send_pattern_email(recipient, image, self.smtp, key, cancel, original=original)
                success = not cancel.is_set()
            except Exception:
                pass  # Provider responses can include private information.
            self.email_results.put((generation, success))
        threading.Thread(target=worker, daemon=True, name="email-worker").start()

    def go(self, direction: int) -> None:
        next_stage = max(0, min(5, self.stage+direction))
        if self.stage == 3 and next_stage == 4 and self.camera:
            # Stage 3 used the robot for explanation. Stage 4 starts with a
            # live view so the visitor can frame their own portrait.
            self.model.cancel()
            self.revision = self.model.revision
            self.session.photo = self.session.result = None
            self.busy = False
            self.status_key = "camera_ready" if self.camera.available else "camera_missing"
        self.stage = next_stage
        self.build_stage()

    def switch_language(self) -> None:
        self.close_email()
        self.language = "en" if self.language == "fr" else "fr"
        self.root.title(self.t("app"))
        self.build_stage()

    def reset(self) -> None:
        if self.debounce:
            self.root.after_cancel(self.debounce)
            self.debounce = None
        self.model.cancel()
        self.revision = self.model.revision
        self.session.clear()
        self.email_cancel.set()
        self.close_email()
        if self.camera:
            self.camera.clear()
        for popup in self.root.winfo_children():
            if isinstance(popup, tk.Toplevel):
                popup.destroy()
        self.photo_refs.clear()
        self.busy, self.preset, self.palette = False, "labyrinth", "ocean"
        self.size.set(1.)
        self.intensity.set(.58)
        self.seed, self.speed, self.playing = 19, "normal", True
        self.frame_index, self.view, self.stage, self.status_key = 0, "portrait", 0, "ready"
        self.build_stage()

    def on_resize(self, event) -> None:
        if event.widget == self.root:
            self.intro.configure(wraplength=max(event.width-60, 800))
            if self.stage in (3, 4):
                self.explanation.configure(wraplength=max(event.width-(400 if self.stage == 4 else 60), 500))
            self.schedule_redraw()

    def toggle_fullscreen(self, _event=None) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def leave_fullscreen(self, _event=None) -> None:
        self.fullscreen = False
        self.root.attributes("-fullscreen", False)

    def close(self) -> None:
        if self.closed:
            return
        self.reset()
        self.closed = True
        self.root.after_cancel(self.poll_id)
        if self.redraw_id:
            self.root.after_cancel(self.redraw_id)
            self.redraw_id = None
        self.model.close()
        if self.camera:
            self.camera.close()
        self.root.destroy()
        # Release Tcl-backed objects on their owning thread. Otherwise cyclic
        # GC triggered by a later numerical worker can run Variable.__del__
        # off-thread after the window has closed.
        self.photo_refs.clear()
        self.thumb_refs.clear()
        self.thumb_buttons.clear()
        self.size = self.intensity = self.status = self.email_var = None
        if hasattr(self, "range_var"):
            self.range_var = None
        for name, value in list(vars(self).items()):
            if isinstance(value, tk.Misc):
                setattr(self, name, None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--language", choices=("fr", "en"), default="fr")
    try:
        camera_index = int(os.getenv("CAMERA_INDEX", "0"))
    except ValueError:
        camera_index = 0
    parser.add_argument("--camera-index", type=int, default=camera_index)
    parser.add_argument("--resolution", type=int, choices=(128, 160, 224, 256, 320), default=224)
    parser.add_argument("--seed", type=int, default=19)
    parser.add_argument("--smoke-test", action="store_true", help="Check journey, comparison, language and reset without webcam/email")
    args = parser.parse_args()
    if args.seed < 0:
        parser.error("--seed must be nonnegative")
    root = tk.Tk()
    app = TuringFairApp(root, demo=args.demo or args.smoke_test, language=args.language,
        fullscreen=args.fullscreen, camera_index=args.camera_index, resolution=args.resolution, seed=args.seed)
    if args.smoke_test:
        deadline, state, errors = time.monotonic()+45, [0], []
        def callback_error(_kind, value, _traceback):
            errors.append(str(value))
            app.close()
        root.report_callback_exception = callback_error
        def smoke() -> None:
            if time.monotonic() > deadline:
                raise RuntimeError("GUI smoke test timed out")
            if state[0] == 0:
                for stage in range(4):
                    app.stage = stage
                    app.build_stage()
                    root.update_idletasks()
                state[0] = 1
            elif state[0] == 1 and app.session.result:
                app.stage = 4
                app.build_stage()
                app.compare_patterns()
                state[0] = 2
            elif state[0] == 2 and app.session.result and app.session.result.comparison:
                app.stage = 5
                app.build_stage()
                app.switch_language()
                app.open_email()
                app.reset()
                assert app.session.photo is None and app.session.result is None and not app.email_var.get()
                print("GUI smoke test passed: journey, comparison, language, optional email, reset")
                app.close()
                return
            root.after(100, smoke)
        root.after(100, smoke)
    root.mainloop()
    if args.smoke_test and errors:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    main()
