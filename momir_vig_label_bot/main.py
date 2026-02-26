import time
from threading import Thread

from telegram.ext import Updater

from momir_vig_label_bot.bot import MomirVigLabelBot
from momir_vig_label_bot.credentials import MOMIR_VIG_LABEL_BOT_TOKEN, MY_ID
from momir_vig_label_bot.logger import get_application_logger

log = get_application_logger()


def main():
    bot = MomirVigLabelBot()
    bot.update_inline_keyboard(str(MY_ID))


def main_loop():
    updater = Updater(token=MOMIR_VIG_LABEL_BOT_TOKEN)
    queue = updater.start_polling()

    bot = MomirVigLabelBot()

    def process_update_fn(new_update):
        bot.process_update(new_update)

    while True:
        try:
            update = queue.get()
            thread = Thread(target=process_update_fn, args=(update, ))
            thread.start()
            # thread.join()  # cause concurrency problems
        except Exception as exc:
            log.error(exc)
            time.sleep(10)


if __name__ == '__main__':
    # main()
    main_loop()
    # pass
