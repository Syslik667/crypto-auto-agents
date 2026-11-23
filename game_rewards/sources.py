"""Public help pages and canonical platform links. Offer access is manual."""
from urllib.parse import urlsplit

from intelligence.net import canonical, failure, fetch

PLATFORMS = {
    "freecash": {
        "name": "Freecash", "home": "https://freecash.com/",
        "rules": "https://freecash.com/academy/en/support/offer/miscellaneous/how-to-start-your-first-offer-on-freecash",
        "payout": "https://freecash.com/academy/de/support/withdrawal/issues/withdrawal-limits",
        "help": "Откройте Earn в своём аккаунте. Сверьте устройство, первый запуск игры, каждый уровень и время от установки.",
    },
    "swagbucks": {
        "name": "Swagbucks", "home": "https://www.swagbucks.com/",
        "rules": "https://help.swagbucks.com/hc/en-us/articles/205640554-Is-Swagbucks-available-in-my-country",
        "payout": "",
        "help": "Сверьте страну, условия конкретной игры и доступный вариант обмена SB. Введите денежный эквивалент только по условиям обмена.",
    },
    "timebucks": {
        "name": "TimeBucks", "home": "https://timebucks.com/",
        "rules": "https://help.timebucks.com/en/articles/12137505-what-is-timebucks",
        "payout": "https://help.timebucks.com/en/articles/10123545-available-payment-methods",
        "help": "Проверяйте расходы, срок и статус каждого предложения в Earn. Выплата зависит от способа и проверки аккаунта.",
    },
}


def offer_url(platform, value):
    if not value:
        return PLATFORMS[platform]["home"]
    url = canonical(value)
    domain = urlsplit(PLATFORMS[platform]["home"]).hostname.removeprefix("www.")
    host = urlsplit(url).hostname or ""
    if not url or not (host == domain or host.endswith("." + domain)):
        raise ValueError("Укажите HTTPS-ссылку на официальном домене выбранной площадки.")
    return url


def public_status(reader=fetch):
    """A reachable help page is evidence of access to help, not of a live offer."""
    checks = {}
    for key, platform in PLATFORMS.items():
        try:
            if not reader(platform["rules"], exact_url=True):
                raise ValueError("Empty documentation")
        except Exception as exc:
            checks[key] = failure(exc)
        else:
            checks[key] = "Справка доступна"
    return checks
