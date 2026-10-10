"""Security identifiers."""

import re

INDIAN_ISIN = re.compile(r"IN[A-Z0-9]{9}[0-9]", re.ASCII)


def is_valid_isin(text: str) -> bool:
    """An Indian ISIN (``IN`` + 9 characters + check digit) whose check digit is right
    (ISO 6166: letters become 10-35, then the Luhn check over the digit string)."""
    if not INDIAN_ISIN.fullmatch(text):
        return False
    digits = "".join(str(int(c, 36)) for c in text)
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char) * (2 if position % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def scrip_key(name: str) -> str:
    """How a company name from a broker's file is compared: upper case, single spaces."""
    return " ".join(name.upper().split())
