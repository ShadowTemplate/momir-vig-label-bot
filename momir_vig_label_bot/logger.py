import logging as log


def get_application_logger():
    log.basicConfig(level=log.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    return log
