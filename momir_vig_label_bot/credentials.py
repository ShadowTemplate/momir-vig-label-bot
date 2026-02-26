import os
from importlib import import_module

from momir_vig_label_bot.constants import SECRETS_UNTRACKED_FILE


def _get_credential_from_secrets(credential_key):
    try:  # will succeed locally if secret.py file is available
        secret_module = import_module(SECRETS_UNTRACKED_FILE.rstrip(".py"))
        # print(getattr(secret_module, 'USERS'))
        return getattr(secret_module, credential_key)
    except ModuleNotFoundError:
        return None


def get_credential(credential_key):
    return os.environ.get(credential_key, _get_credential_from_secrets(credential_key))


MOMIR_VIG_LABEL_BOT_TOKEN = get_credential('MOMIR_VIG_LABEL_BOT_TOKEN')
MY_ID = get_credential('MY_ID')
