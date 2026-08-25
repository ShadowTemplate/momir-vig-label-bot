SECRETS_UNTRACKED_FILE = "momir_vig_label_bot.secrets.py"
MAX_MANA_VALUE = 19
SCRYFALL_RANDOM_CARD = "https://api.scryfall.com/cards/random?q=type:creature+mv:"
# Scryfall requires every client to identify itself with a User-Agent, both on
# the API and on the image CDN. Anonymous requests are rejected.
SCRYFALL_USER_AGENT = "MomirVigLabelBot/1.0"

# Telegram rejects any photo whose width/height ratio exceeds this, so a label
# thinner than that has to be padded before it can be sent as a picture.
TELEGRAM_MAX_PHOTO_RATIO = 20

# --- SUPVAN / Katasymbol E10 label printer ---
# Bluetooth MAC of the printer. Left unset on purpose: a MAC identifies
# somebody's physical device, so it does not belong in the repo. When empty the
# driver finds the printer among the paired Bluetooth devices by Supvan's OUI.
# Override via the E10_MAC env var or secrets.py.
E10_MAC_DEFAULT = ""
# Burn energy 0-15. 8 prints cleanly on genuine Supvan stock.
E10_DENSITY = 8
# Minimum label length in mm; wordier cards produce longer labels, never
# truncated text.
E10_TARGET_MM = 70
# Interpreter used for the printing subprocess. Must have socket.AF_BLUETOOTH,
# which the bot's own venv (Python 3.9) lacks. Override with E10_PYTHON.
E10_PYTHON_DEFAULT = "/usr/bin/python3"
