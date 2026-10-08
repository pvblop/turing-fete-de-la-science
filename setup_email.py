"""Operator-only email setup. Secrets stay in this process; no files are written.

Run in a terminal: python setup_email.py --fullscreen
Or: python setup_email.py --provider smtp --fullscreen
Only pressing Send in the kiosk transmits an email; setup does not send a test.
"""
from __future__ import annotations
import argparse
import getpass
import os
import sys
import warnings
from collections.abc import Callable, Mapping
from turing_services import EMAIL_RE

SMTP_KEYS = ('SMTP_HOST', 'SMTP_PORT', 'SMTP_FROM', 'SMTP_USER', 'SMTP_PASSWORD',
             'SMTP_USE_TLS', 'SMTP_USE_SSL')


def email_settings(sender: str, username: str, password: str, *, host: str = 'smtp.gmail.com',
                   port: int = 587, security: str = 'starttls') -> dict[str, str]:
    """Build a validated session configuration, without contacting the provider."""
    sender = sender.strip()
    if not EMAIL_RE.fullmatch(sender):
        raise ValueError('Enter a valid sender address.')
    if not host or any(c.isspace() for c in host) or not 0 < port < 65536:
        raise ValueError('Check the SMTP host and port.')
    if security not in ('starttls', 'ssl'):
        raise ValueError('Choose starttls or ssl for encrypted email.')
    if username and not password:
        raise ValueError('The sending account needs a password or App Password.')
    return dict(SMTP_HOST=host, SMTP_PORT=str(port), SMTP_FROM=sender,
                SMTP_USER=username, SMTP_PASSWORD=password,
                SMTP_USE_TLS=str(security == 'starttls').lower(),
                SMTP_USE_SSL=str(security == 'ssl').lower())


def launch_with_email(settings: Mapping[str, str], app_args: list[str],
                      run: Callable[[], None] | None = None) -> None:
    """Temporarily set environment variables and run the existing kiosk entry point."""
    previous = {key: os.environ.get(key) for key in SMTP_KEYS}
    previous_args = sys.argv
    try:
        os.environ.update(settings)
        sys.argv = ['turing_fair_app.py', *app_args]
        if run is None:
            from turing_fair_app import main
            run = main
        run()
    finally:
        sys.argv = previous_args
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def hidden_password() -> str:
    """Refuse getpass's echoed-input fallback if a terminal cannot hide the secret."""
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        return getpass.getpass('SMTP password / Gmail App Password (hidden): ')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('gmail', 'smtp'), default=None)
    args, app_args = parser.parse_known_args(argv)
    print('Festival operator email setup — credentials are not saved to a file.')
    print('You need a sending account or an authorised institutional SMTP relay.')
    print('A no-reply address works only when your provider has authorised that address.')
    try:
        provider = args.provider or input('Sender service [gmail/smtp, default gmail]: ').strip().lower() or 'gmail'
        if provider not in ('gmail', 'smtp'):
            raise ValueError('Choose gmail or smtp.')
        sender = input('Sending account / authorised From address: ').strip()
        if not EMAIL_RE.fullmatch(sender):
            raise ValueError('Enter a valid sender address.')
        if provider == 'gmail':
            print('Use a Gmail App Password with 2-Step Verification enabled; not your normal password.')
            print('Instructions: https://support.google.com/accounts/answer/185833')
            settings = email_settings(sender, sender, hidden_password().replace(' ', ''))
        else:
            host = input('SMTP host supplied by your institution/provider: ').strip()
            security = input('Security [starttls/ssl, default starttls]: ').strip().lower() or 'starttls'
            port_text = input(f'SMTP port [default {465 if security == "ssl" else 587}]: ').strip()
            port = int(port_text or (465 if security == 'ssl' else 587))
            username = input('SMTP username [default sender address; enter - for an unauthenticated relay]: ').strip()
            username = '' if username == '-' else username or sender
            password = hidden_password() if username else ''
            settings = email_settings(sender, username, password, host=host, port=port, security=security)
        print('Configuration loaded for this session. Delivery has not been tested.')
        print('To test, use the robot and send a portrait to your own address from the kiosk.')
        print('Only an explicit press of Send transmits a message. Closing the kiosk ends this session.')
        launch_with_email(settings, app_args)
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nSetup cancelled; no email was sent.')
    except getpass.GetPassWarning:
        print('Run this script in a real terminal so password input can be hidden.')
    except ValueError as exc:
        print(f'Setup incomplete: {exc}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
