import os
from importlib import import_module

from momir_vig_label_bot.constants import (
    SECRETS_UNTRACKED_FILE, E10_MAC_DEFAULT, E10_PYTHON_DEFAULT)


def _get_credential_from_secrets(credential_key):
    try:  # will succeed locally if secret.py file is available
        secret_module = import_module(SECRETS_UNTRACKED_FILE.rstrip(".py"))
        # print(getattr(secret_module, 'USERS'))
        return getattr(secret_module, credential_key)
    except (ModuleNotFoundError, AttributeError):
        # AttributeError: the key simply isn't in secrets.py; callers that have
        # a sensible default (E10_MAC) should get None rather than a crash.
        return None


def get_credential(credential_key):
    return os.environ.get(credential_key, _get_credential_from_secrets(credential_key))


MOMIR_VIG_LABEL_BOT_TOKEN = get_credential('MOMIR_VIG_LABEL_BOT_TOKEN')
MY_ID = get_credential('MY_ID')
E10_MAC = get_credential('E10_MAC') or E10_MAC_DEFAULT
E10_PYTHON = get_credential('E10_PYTHON') or E10_PYTHON_DEFAULT
