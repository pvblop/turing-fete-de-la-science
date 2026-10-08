"""Validate operator setup without real credentials, network or sending mail."""
import os
import sys
from unittest.mock import patch
import pytest
from setup_email import SMTP_KEYS, email_settings, hidden_password, launch_with_email, main
from turing_services import SMTPConfig


def test_sender_credentials_exist_only_for_the_launched_session(monkeypatch):
    for key in SMTP_KEYS:
        monkeypatch.delenv(key, raising=False)
    original_args = sys.argv
    settings = email_settings('stand@example.invalid', 'stand@example.invalid', 'test-only-placeholder')
    observed = []
    def fake_launch():
        observed.append(SMTPConfig().ready)
        assert sys.argv == ['turing_fair_app.py', '--fullscreen']
    with patch('builtins.open', side_effect=AssertionError('Do not write credentials')):
        launch_with_email(settings, ['--fullscreen'], run=fake_launch)
    assert observed == [True]
    assert sys.argv is original_args
    assert all(key not in os.environ for key in SMTP_KEYS)


def test_environment_is_restored_even_if_the_app_fails(monkeypatch):
    for key in SMTP_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SMTP_HOST', 'previous.example.invalid')
    settings = email_settings('stand@example.invalid', '', '', host='relay.example.invalid', port=465, security='ssl')
    def fails():
        raise RuntimeError('Simulated application failure')
    with pytest.raises(RuntimeError):
        launch_with_email(settings, [], run=fails)
    assert os.environ['SMTP_HOST'] == 'previous.example.invalid'
    assert 'SMTP_PASSWORD' not in os.environ


@pytest.mark.parametrize('kwargs', [dict(sender='invalid'), dict(password=''), dict(port=0), dict(security='plain')])
def test_invalid_sender_configuration_is_rejected(kwargs):
    inputs = dict(sender='stand@example.invalid', username='stand@example.invalid', password='test-only-placeholder')
    inputs.update(kwargs)
    with pytest.raises(ValueError):
        email_settings(**inputs)


def test_no_echoed_password_fallback():
    import getpass
    import warnings
    def unsafe_prompt(_prompt):
        warnings.warn('Cannot hide input', getpass.GetPassWarning)
        return 'never-use-an-echoed-secret'
    with patch('setup_email.getpass.getpass', unsafe_prompt), pytest.raises(getpass.GetPassWarning):
        hidden_password()


def test_authentication_without_a_password_is_not_ready(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.invalid')
    monkeypatch.setenv('SMTP_PORT', '587')
    monkeypatch.setenv('SMTP_FROM', 'stand@example.invalid')
    monkeypatch.setenv('SMTP_USER', 'stand@example.invalid')
    monkeypatch.setenv('SMTP_PASSWORD', '')
    monkeypatch.setenv('SMTP_USE_TLS', 'true')
    monkeypatch.setenv('SMTP_USE_SSL', 'false')
    assert not SMTPConfig().ready


def test_guided_gmail_setup_passes_flags_and_does_not_print_password(capsys):
    with patch('builtins.input', return_value='stand@example.invalid'), \
         patch('setup_email.hidden_password', return_value='test-only-placeholder'), \
         patch('setup_email.launch_with_email') as launch:
        assert main(['--provider', 'gmail', '--fullscreen', '--demo']) == 0
    settings, flags = launch.call_args[0]
    assert settings['SMTP_FROM'] == 'stand@example.invalid'
    assert settings['SMTP_HOST'] == 'smtp.gmail.com'
    assert flags == ['--fullscreen', '--demo']
    assert 'test-only-placeholder' not in capsys.readouterr().out


def test_invalid_sender_is_rejected_before_asking_for_a_secret():
    with patch('builtins.input', return_value='invalid'), patch('setup_email.hidden_password') as password:
        assert main(['--provider', 'gmail']) == 1
        password.assert_not_called()
