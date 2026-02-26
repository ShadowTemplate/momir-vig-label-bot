import json
import os
import subprocess
import time
import telegram
import requests
from telegram import InlineKeyboardButton, ReplyKeyboardMarkup

from momir_vig_label_bot.constants import MAX_MANA_VALUE, SCRYFALL_RANDOM_CARD
from momir_vig_label_bot.credentials import MOMIR_VIG_LABEL_BOT_TOKEN, MY_ID
from momir_vig_label_bot.logger import get_application_logger

log = get_application_logger()

class MomirVigLabelBot:

    def __init__(self):
        self._bot = telegram.Bot(token=MOMIR_VIG_LABEL_BOT_TOKEN)

    def process_update(self, update):
        log.info(update)
        log.info(f"Processing new message...")
        if not hasattr(update, 'message'):
            self._bot.send_message(
                chat_id=MY_ID,
                text=f"Update from unknown user: {update}.",
            )
        elif hasattr(update.message, 'text'):
            chat_id = str(update['message']['chat']['id'])
            if update.message.text == "/start":
                self.update_inline_keyboard(chat_id)
            elif update.message.text in list(str(i) for i in range(MAX_MANA_VALUE + 1)):
                self.generate_label(chat_id, update.message.text)
            else:
                self.send_error_message(chat_id)
        else:
            self._bot.send_message(
                chat_id=MY_ID,
                text=f"Unknown message update type: {update}.",
            )
        log.info(f"Processed new message.")

    def process_batch_updates(self):
        log.info(f"Processing updates...")
        for update in self._bot.get_updates():
            self.process_update(update)
        log.info(f"Processed updates.")

    def get_reply_keyboard(self):
        buttons = []
        numbers = list(range(0, MAX_MANA_VALUE + 1))
        for i in range(0, len(numbers), 4):
            row = []
            for n in numbers[i:i + 4]:
                row.append(
                    InlineKeyboardButton(
                        text=str(n),
                        callback_data=str(n)
                    )
                )
            buttons.append(row)
        return ReplyKeyboardMarkup(buttons)

    def update_inline_keyboard(self, chat_id):
        message = "Good luck and have fun!"
        self._bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=self.get_reply_keyboard(),
        )

    def send_error_message(self, chat_id):
        self._bot.send_message(
            chat_id=chat_id,
            text="Invalid mana value!\nUse one button below!",
            reply_markup=self.get_reply_keyboard(),
        )

    def generate_label(self, chat_id, mana_value):
        self._bot.send_message(
            chat_id=chat_id,
            text=f"Generating creature with mana value {mana_value}...",
        )
        card, r = self.get_random_card(mana_value)
        if not card:
            try:
                error = json.loads(r.text)['details']
            except Exception:
                error = r.status_code
            self._bot.send_message(
                chat_id=chat_id,
                text=f"Unable to generate label, try again!\n\nError: {error}",
            )
            return
        log.debug(card)
        log.debug(f"Sending card picture...")
        self._bot.send_photo(
            chat_id=chat_id,
            photo=f"{card['png']}",
            caption=f"{card['url']}",
        )
        text = f"{card['name']} {card['mana_cost']} | {card['type_line']} | {card['pt']}"
        if card['text']:
            text += f"\n{card['text']}"
        log.debug(f"Sending card text...")
        self._bot.send_message(
            chat_id=chat_id,
            text=text,
        )
        log.debug(f"Generating label...")
        now = str(int(time.time() * 1000))
        os.makedirs("/tmp/momir/", exist_ok=True)
        oracle_text_path = f"/tmp/momir/{now}.txt"
        tmp_buffer_path = f"/tmp/momir/{now}_tmp.png"
        label_image_path = f"/tmp/momir/{now}.png"
        with open(oracle_text_path, "w") as out_f:
            out_f.write(text)
        log.debug(f"Running Linux commands...")
        convert_cmd = (f"pango-view --font=\"Helvetica 24\" --width=800 --no-display "
                       f"--output={tmp_buffer_path} {oracle_text_path} && "
                       f"convert {tmp_buffer_path} -trim +repage -bordercolor white "
                       f"-border 5x5 {label_image_path} && "
                       f"rm {tmp_buffer_path}")
        log.debug(convert_cmd)
        result = subprocess.run(
            convert_cmd,
            shell=True,  # needed for && and shell syntax
            capture_output=True,  # captures stdout + stderr
            text=True  # decode bytes to str
        )

        if result.returncode != 0:
            log.warning(f"Linux command failed with return code {result.returncode}")
            log.warning(f"{result.stdout}")
            log.warning(f"{result.stderr}")
            self._bot.send_message(
                chat_id=chat_id,
                text="Unable to generate label, try again!",
            )
        else:
            log.debug(f"Successfully generated label!")
            with open(label_image_path, "rb") as label_image:
                self._bot.send_photo(
                    chat_id=chat_id,
                    photo=label_image,
                )
        subprocess.run(
            f"rm {label_image_path} {oracle_text_path} {tmp_buffer_path}",
            shell=True,  # needed for && and shell syntax
            capture_output=True,  # captures stdout + stderr
            text=True  # decode bytes to str
        )

    def get_random_card(self, mana_value):
        r = requests.get(SCRYFALL_RANDOM_CARD + mana_value)
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as e:
            log.warning(e)
            log.warning(r.status_code)
            log.warning(r.text)
            return None, r
        except requests.exceptions.RequestException as e:
            log.warning(e)
            return None, r
        data = r.json()
        return {
            "name": data["name"],
            "mana_cost": data.get("mana_cost", ""),
            "type_line": data.get("type_line", ""),
            "text": data.get("oracle_text", ""),
            "pt": data.get("power", "") + "/" + data.get("toughness", ""),
            "url": data["scryfall_uri"],
            "png": data["image_uris"]["normal"],
        }, r
