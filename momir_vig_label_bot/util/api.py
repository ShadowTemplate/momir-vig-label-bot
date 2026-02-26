from sciordo_bot.constants import MAX_API_RETRY_ATTEMPTS
from tenacity import (
    retry,
    retry_if_exception_message,
    stop_after_attempt,
    wait_random_exponential,
)


def retry_gspread(func):
    @retry(
        # In the beginning, I was using a generic
        # retry=retry_if_exception_type(APIError)
        # but that doesn't really work, because you risk to get stuck retrying
        # malformed requests for a long time.
        # It's better to be strict and retry only what you know needs to be retried.
        # FYI, sometimes I've seen this being raised:
        # gspread.exceptions.APIError:
        # {'code': 500, 'message': 'Internal error encountered.', 'status': 'INTERNAL'}
        retry=retry_if_exception_message(match=r".*Quota exceeded.*"),
        stop=stop_after_attempt(MAX_API_RETRY_ATTEMPTS),
        wait=wait_random_exponential(multiplier=1, max=60),
    )
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper
