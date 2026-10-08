"""Reset boundaries, stale-job rejection, translations, and mocked SMTP."""
import threading
import time
import io
from unittest.mock import MagicMock, patch
from PIL import Image
import pytest
from turing_i18n import TRANSLATIONS
from turing_models import PRESETS, demo_image
from turing_services import CameraWorker, ModelWorker, SMTPConfig, VisitorSession, postcard, send_pattern_email, _postcard_font


def test_translation_keys_and_placeholders():
    import string
    fr, en = TRANSLATIONS['fr'], TRANSLATIONS['en']
    assert fr.keys() == en.keys()
    formatter = string.Formatter()
    for key in fr:
        assert {f for _,f,_,_ in formatter.parse(fr[key]) if f} == {f for _,f,_,_ in formatter.parse(en[key]) if f}
        assert fr[key] and en[key]
    for key in PRESETS:
        assert key+'_name' in fr and key+'_desc' in fr


def test_session_clears_all_visitor_data():
    session = VisitorSession(photo=demo_image(), result=object(), email='visitor@example.invalid')
    session.clear()
    assert session.photo is None and session.result is None and session.email == ''
    assert session.generation == 1


def test_latest_job_wins_and_reset_discards_result():
    worker = ModelWorker(thumbnails=False)
    request = dict(image=demo_image(), preset='labyrinth', seed=2, size=1., intensity=.6, palette='ocean', resolution=80)
    try:
        for _ in range(8):
            revision = worker.submit(**request)
        deadline = time.monotonic()+10
        result = None
        while time.monotonic() < deadline:
            result, error, _, _ = worker.take()
            assert error is None
            if result:
                break
            time.sleep(.01)
        assert result and result.revision == revision
        assert result.email_image.size == (900, 900)
        assert len(result.frames) == 25
        worker.submit(**request)
        worker.cancel()
        time.sleep(.15)
        assert worker.take()[0] is None
        assert worker.pending is None
    finally:
        worker.close()
    assert not worker.thread.is_alive()


def smtp_config(monkeypatch):
    for name in ('SMTP_HOST','SMTP_PORT','SMTP_USER','SMTP_PASSWORD','SMTP_FROM','SMTP_USE_TLS','SMTP_USE_SSL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.invalid')
    monkeypatch.setenv('SMTP_FROM', 'stand@example.invalid')
    return SMTPConfig()


def test_email_in_memory_and_cancel_before_transport(monkeypatch):
    config = smtp_config(monkeypatch)
    transport = MagicMock()
    smtp = transport.return_value.__enter__.return_value
    original = Image.new('RGB', (640, 480), (187, 33, 91))
    pattern = Image.new('RGB', (900, 900), (21, 174, 88))
    with patch('turing_services.smtplib.SMTP', transport), patch('builtins.open', side_effect=AssertionError('No disk I/O')):
        send_pattern_email('visitor@example.invalid', pattern, config, 'labyrinth', original=original)
    smtp.starttls.assert_called_once()
    message = smtp.send_message.call_args[0][0]
    attachment = next(message.iter_attachments())
    assert attachment.get_content_type() == 'image/png'
    assert attachment.get_content().startswith(b'\x89PNG')
    assert attachment.get_filename() == 'turing_collage.png'
    with Image.open(io.BytesIO(attachment.get_content())) as collage:
        assert collage.size == (1872, 1144)
        assert collage.getpixel((474, 514)) == (187, 33, 91)
        assert collage.getpixel((1398, 514)) == (21, 174, 88)
    assert original.size == (640, 480) and pattern.size == (900, 900)
    assert 'visitor@example.invalid' not in message.get_body().get_content()
    cancellation = threading.Event()
    cancellation.set()
    with patch('turing_services.smtplib.SMTP') as mocked:
        send_pattern_email('visitor@example.invalid', demo_image(), config, cancel=cancellation)
        mocked.assert_not_called()


def test_single_portrait_format_remains_available():
    # Existing callers that do not supply an original still get a valid card.
    assert postcard(demo_image(), 'labyrinth').size == (900, 1080)


def test_postcard_font_supports_french_accents():
    font = _postcard_font(20)
    missing_glyph = bytes(font.getmask('\uffff'))
    for letter in 'éèêàç':
        assert bytes(font.getmask(letter)) != missing_glyph


def test_bad_port_and_conflicting_tls_disable_email(monkeypatch):
    smtp_config(monkeypatch)
    monkeypatch.setenv('SMTP_PORT', 'bad')
    assert not SMTPConfig().ready
    monkeypatch.setenv('SMTP_PORT', '465')
    monkeypatch.setenv('SMTP_USE_SSL', 'true')
    monkeypatch.setenv('SMTP_USE_TLS', 'true')
    assert not SMTPConfig().ready


@pytest.mark.parametrize('opens', [True, False])
def test_camera_is_owned_and_released_by_worker(opens):
    import numpy as np
    camera = MagicMock()
    camera.isOpened.return_value = opens
    camera.read.return_value = (True, np.zeros((60, 80, 3), dtype=np.uint8))
    with patch('turing_services.cv2.VideoCapture', return_value=camera):
        worker = CameraWorker(0)
        try:
            deadline = time.monotonic()+2
            while opens and worker.latest() is None and time.monotonic() < deadline:
                time.sleep(.01)
            assert (worker.latest() is not None) == opens
        finally:
            worker.close()
        camera.release.assert_called_once()
        assert not worker.thread.is_alive()
        assert worker.frame is None
