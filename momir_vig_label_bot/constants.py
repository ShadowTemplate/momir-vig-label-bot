SECRETS_UNTRACKED_FILE = "momir_vig_label_bot.secrets.py"
MAX_MANA_VALUE = 19
SCRYFALL_RANDOM_CARD = "https://api.scryfall.com/cards/random?q=type:creature+mv:"

# --- SUPVAN / Katasymbol E10 label printer ---
# Bluetooth MAC of the printer. Override with the E10_MAC env var or secrets.py.
E10_MAC_DEFAULT = "A4:93:40:B7:3B:F0"
# Burn energy 0-15. 8 prints cleanly on genuine Supvan stock.
E10_DENSITY = 8
# Minimum label length in mm; wordier cards produce longer labels, never
# truncated text.
E10_TARGET_MM = 70
# Interpreter used for the printing subprocess. Must have socket.AF_BLUETOOTH,
# which the bot's own venv (Python 3.9) lacks. Override with E10_PYTHON.
E10_PYTHON_DEFAULT = "/usr/bin/python3"
