"""Headful Tk checks; no real webcam or SMTP required. Skip without a display."""
import time
import gc
import os
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch
import pytest
from turing_fair_app import TuringFairApp
from turing_models import demo_image


@pytest.fixture(scope='module')
def running_kiosk():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        if os.name == 'nt':
            raise
        pytest.skip(f'A desktop display is required for Tk checks: {exc}')
    app = TuringFairApp(root, demo=True, resolution=128)
    errors = []
    root.report_callback_exception = lambda _type, value, _trace: errors.append(value)
    yield app
    app.close()
    assert not errors
    # A festival runs one Tcl interpreter for many visitors. Collect destroyed
    # widget cycles on its owning thread before later numerical-only tests.
    del app, root
    gc.collect()


@pytest.fixture
def kiosk(running_kiosk):
    running_kiosk.reset()
    yield running_kiosk
    running_kiosk.reset()


def pump(app, predicate, timeout=12):
    deadline = time.monotonic()+timeout
    while not predicate() and time.monotonic() < deadline:
        app.root.update()
        time.sleep(.01)
    assert predicate(), 'GUI operation timed out'


@pytest.mark.parametrize('language', ['fr', 'en'])
@pytest.mark.parametrize('geometry', ['1366x768', '1920x1080'])
def test_journey_widgets_fit_and_reset(kiosk, language, geometry):
    app = kiosk
    app.language = language
    app.root.geometry(geometry)
    for stage in range(6):
        app.stage = stage
        app.build_stage()
        app.root.update()
        w, h = app.root.winfo_width(), app.root.winfo_height()
        assert (w, h) == tuple(map(int, geometry.split('x')))
        assert app.next_button.winfo_rooty()+app.next_button.winfo_height() <= app.root.winfo_rooty()+h
        def check(widget):
            if isinstance(widget, (ttk.Button, ttk.Combobox, ttk.Scale)) and widget.winfo_ismapped():
                x, y = widget.winfo_rootx()-app.root.winfo_rootx(), widget.winfo_rooty()-app.root.winfo_rooty()
                assert x >= 0 and y >= 0
                assert x+widget.winfo_width() <= w, f'Control exceeds window width: {widget}'
                assert y+widget.winfo_height() <= h, f'Control exceeds window height: {widget}'
            for child in widget.winfo_children():
                check(child)
        check(app.shell)
    app.email_var.set('visitor@example.invalid')
    old_refs = list(app.photo_refs)
    app.reset()
    assert app.session.photo is None and app.session.result is None
    assert not app.email_var.get()
    assert all(ref not in app.photo_refs for ref in old_refs)
    assert app.model.pending is None


def test_rapid_controls_cancel_and_language_keeps_photo(kiosk):
    app = kiosk
    app.stage = 4
    app.session.photo = demo_image()
    app.build_stage()
    for size in [.7, 1.8, 1.2, 1.]:
        app.size.set(size)
        app.controls_changed()
    pump(app, lambda: app.session.result is not None)
    result = app.session.result
    app.switch_language()
    assert app.session.result is result
    app.pause()
    assert not app.playing
    app.replay()
    assert app.frame_index == 0 and app.playing
    app.controls_changed()
    app.reset()
    pump(app, lambda: not app.busy, timeout=1)
    assert app.session.result is None


def test_old_email_completion_does_not_affect_new_visitor(kiosk):
    app = kiosk
    app.stage = 3
    app.build_stage()
    pump(app, lambda: app.session.result is not None)
    app.stage = 5
    app.build_stage()
    app.smtp.host, app.smtp.sender = 'smtp.example.invalid', 'stand@example.invalid'
    app.smtp.user = ''
    app.email_var.set('visitor@example.invalid')
    import threading
    started, release = threading.Event(), threading.Event()
    sent_originals = []
    expected_photo = app.session.result.original.tobytes()
    def held_send(*_args, **kwargs):
        sent_originals.append(kwargs['original'])
        started.set()
        release.wait(2)
    with patch('turing_fair_app.send_pattern_email', held_send):
        app.send_email()
        assert started.wait(1)
        assert sent_originals[0].tobytes() == expected_photo
        assert sent_originals[0] is not app.session.result.original
        assert not app.email_var.get()
        app.reset()
        release.set()
        pump(app, lambda: not app.email_busy)
    assert app.status_key == 'ready'
    assert app.session.photo is None and app.session.result is None


@pytest.mark.parametrize('geometry', ['1366x768', '1920x1080'])
def test_photo_and_pattern_have_matching_display_dimensions(kiosk, geometry):
    app = kiosk
    app.root.geometry(geometry)
    app.stage = 4
    # A landscape camera frame and a much smaller simulation must share the
    # same square crop and viewport, rather than each keeping its native size.
    app.session.photo = demo_image().resize((960, 540))
    app.build_stage()
    app.request_transform()
    pump(app, lambda: app.session.result is not None)
    app.root.update_idletasks()
    app.display_frame()
    assert app.session.result.original.size == (540, 540)
    assert len(app.photo_refs) == 2
    sizes = [(image.width(), image.height()) for image in app.photo_refs]
    assert sizes[0] == sizes[1]
    assert sizes[0][0] > app.resolution  # test actual enlargement, not just fit
    assert sizes[0][0] == sizes[0][1]
    for mode in ('diffusion', 'portrait'):
        app.view = mode
        app.display_frame()
        assert app.photo_refs[0].width() == app.photo_refs[1].width()
    # While paused, changing the window must redraw after canvas layout.
    app.playing = False
    app.root.geometry('1500x850')
    app.root.update()
    app.root.update_idletasks()
    assert app.photo_refs[0].width() == app.photo_refs[1].width()


def test_removed_presets_do_not_appear_in_selector(kiosk):
    app = kiosk
    app.stage = 4
    app.build_stage()
    assert set(app.thumb_buttons) == {'stripes', 'labyrinth', 'holes', 'fine'}
