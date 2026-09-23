from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

from app.core.enums import PIIType
from app.core.ru_text import GENITIVE_MONTHS
from app.replacement.morphology import guess_gender, inflect_full_name

MALE_FIRST = [
    "Иван",
    "Александр",
    "Дмитрий",
    "Алексей",
    "Сергей",
    "Михаил",
    "Николай",
    "Андрей",
    "Павел",
    "Максим",
    "Антон",
    "Юрий",
    "Пётр",
    "Лев",
    "Роман",
    "Виктор",
]
FEMALE_FIRST = [
    "Анна",
    "Мария",
    "Елена",
    "Ольга",
    "Наталья",
    "Екатерина",
    "Ирина",
    "Алина",
    "Дарья",
    "Светлана",
    "Татьяна",
    "Юлия",
    "Полина",
    "Виктория",
    "Ксения",
]
MALE_LAST = [
    "Иванов",
    "Петров",
    "Смирнов",
    "Кузнецов",
    "Соколов",
    "Попов",
    "Лебедев",
    "Козлов",
    "Новиков",
    "Морозов",
    "Волков",
    "Егоров",
    "Захаров",
    "Фролов",
]
FEMALE_LAST = [s + "а" for s in MALE_LAST]
MALE_PATR = [
    "Иванович",
    "Александрович",
    "Сергеевич",
    "Петрович",
    "Алексеевич",
    "Михайлович",
    "Николаевич",
    "Андреевич",
    "Дмитриевич",
    "Викторович",
]
FEMALE_PATR = [
    "Ивановна",
    "Александровна",
    "Сергеевна",
    "Петровна",
    "Алексеевна",
    "Михайловна",
    "Николаевна",
    "Андреевна",
    "Дмитриевна",
    "Викторовна",
]

CITIES = [
    "Москва",
    "Санкт-Петербург",
    "Казань",
    "Екатеринбург",
    "Новосибирск",
    "Самара",
    "Воронеж",
    "Пермь",
]
STREETS = [
    "Ленина",
    "Мира",
    "Гагарина",
    "Садовая",
    "Советская",
    "Победы",
    "Лесная",
    "Центральная",
]
COUNTRIES = [
    "Россия",
    "Российская Федерация",
    "Беларусь",
    "Казахстан",
    "Армения",
    "Кыргызстан",
    "Узбекистан",
    "Таджикистан",
    "Азербайджан",
    "Молдова",
    "Туркменистан",
    "Грузия",
]
EMAIL_LOCAL_POOL = ["ivan", "alex", "maria", "anna", "dmitry", "client", "user", "info"]
EMAIL_DOMAIN_POOL = ["mail.ru", "example.com", "yandex.ru", "inbox.ru", "gmail.com"]
ISSUER_PREFIXES = [
    "ГУ МВД России по г.",
    "УМВД России по г.",
    "Отделом МВД России по г.",
]

_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")
_MONTHS = GENITIVE_MONTHS


class _DeterministicStream:
    """Uniform draws from SHA-256 in counter mode.

    Surrogates must be reproducible for the same (payload_id, entity) so that
    concurrent recomputation of one request always agrees -- a keyed hash
    stream gives that determinism from a cryptographic primitive.
    """

    def __init__(self, seed: int) -> None:
        self._prefix = str(seed).encode()
        self._counter = 0

    def _randbelow(self, n: int) -> int:
        # 8-byte draws with rejection sampling: exactly uniform on [0, n).
        limit = (2**64 // n) * n
        while True:
            payload = self._prefix + b":" + str(self._counter).encode()
            self._counter += 1
            value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
            if value < limit:
                return value % n

    def randint(self, low: int, high: int) -> int:
        """Inclusive bounds, mirroring random.randint."""
        return low + self._randbelow(high - low + 1)

    def choice(self, seq):
        return seq[self._randbelow(len(seq))]


class SyntheticGenerator:
    """Deterministic given `seed` -- same (payload_id, original value) always yields
    the same synthetic replacement, so concurrent recomputation of the same request
    never disagrees (see MappingStore docstring for why that matters).
    """

    def __init__(self, seed: int) -> None:
        self._rng = _DeterministicStream(seed)

    def person(self, original_nominal: str, gender: str | None = None) -> dict:
        gender = gender or guess_gender(original_nominal.split()[0] if original_nominal else "")
        first_pool, last_pool, patr_pool = (
            (MALE_FIRST, MALE_LAST, MALE_PATR)
            if gender == "masc"
            else (FEMALE_FIRST, FEMALE_LAST, FEMALE_PATR)
        )
        for _ in range(10):
            first = self._rng.choice(first_pool)
            last = self._rng.choice(last_pool)
            patr = self._rng.choice(patr_pool)
            nominal = f"{last} {first} {patr}"
            if nominal != original_nominal:
                break
        return {
            "surname": last,
            "first": first,
            "patronymic": patr,
            "gender": gender,
            "nominal": nominal,
        }

    def full_name_in_case(self, original_nominal: str, gender: str | None, case: str) -> tuple[str, dict]:
        identity = self.person(original_nominal, gender)
        surface = inflect_full_name(
            identity["surname"],
            identity["first"],
            identity["patronymic"],
            case,
            identity["gender"],
        )
        return surface, identity

    def date_like(self, value: str) -> str:
        fmt = self._detect_date_format(value)
        start, end = date(1945, 1, 1), date(2008, 12, 31)
        candidate = value
        for _ in range(10):
            d = start + timedelta(days=self._rng.randint(0, (end - start).days))
            candidate = self._format_date(d, fmt)
            if candidate != value:
                break
        return candidate

    def _detect_date_format(self, value: str) -> str:
        for fmt in _DATE_FORMATS:
            try:
                from datetime import datetime

                datetime.strptime(value, fmt)
                return fmt
            except ValueError:
                continue
        return "text"

    def _format_date(self, d: date, fmt: str) -> str:
        if fmt == "text":
            return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"
        return d.strftime(fmt)

    def digits_like(self, value: str, keep_prefix_digits: int = 0) -> str:
        out = []
        seen = 0
        for ch in value:
            if ch.isdecimal():
                out.append(ch if seen < keep_prefix_digits else str(self._rng.randint(0, 9)))
                seen += 1
            else:
                out.append(ch)
        result = "".join(out)
        if result != value:
            return result
        # Bounded fallback: recursion never terminates when there are no mutable
        # digits (and is unnecessary when a random draw repeats the original).
        mutable = [i for i, ch in enumerate(value) if ch.isdecimal()][max(0, keep_prefix_digits) :]
        if not mutable:
            raise ValueError("value has no mutable decimal digits")
        index = mutable[-1]
        out[index] = str((int(value[index]) + 1) % 10)
        return "".join(out)

    def email(self, value: str) -> str:
        for _ in range(10):
            local = f"{self._rng.choice(EMAIL_LOCAL_POOL)}{self._rng.randint(10, 99999)}"
            candidate = f"{local}@{self._rng.choice(EMAIL_DOMAIN_POOL)}"
            if candidate != value:
                return candidate
        return candidate

    def phone(self, value: str) -> str:
        return self.digits_like(value, keep_prefix_digits=1)

    def inn(self, value: str) -> str:
        length = len(re.sub(r"\D", "", value))
        digits = [self._rng.randint(0, 9) for _ in range(length - 1)]
        if length == 10:
            check = _inn10_checksum(digits)
            digits.append(check)
        elif length == 12:
            d11, d12 = _inn12_checks(digits[:10])
            digits = [*digits[:10], d11, d12]
        return "".join(map(str, digits))

    def card_number(self, value: str) -> str:
        body = self._rng.choice(["4", "5", "2"]) + "".join(str(self._rng.randint(0, 9)) for _ in range(14))
        digits = body + _luhn_check_digit(body)
        if "-" in value:
            return "-".join(digits[i : i + 4] for i in range(0, 16, 4))
        if " " in value:
            return " ".join(digits[i : i + 4] for i in range(0, 16, 4))
        return digits

    def department_code(self, value: str) -> str:
        for _ in range(10):
            candidate = f"{self._rng.randint(0, 999):03d}-{self._rng.randint(0, 999):03d}"
            if candidate != value:
                return candidate
        return candidate

    def driver_license(self, value: str) -> str:
        return self.digits_like(value)

    def passport_number(self, value: str) -> str:
        return self.digits_like(value)

    def citizenship(self, value: str) -> str:
        options = [c for c in COUNTRIES if c != value] or COUNTRIES
        return self._rng.choice(options)

    def passport_issuer(self, value: str) -> str:
        for _ in range(10):
            candidate = f"{self._rng.choice(ISSUER_PREFIXES)} {self._rng.choice(CITIES)}"
            if candidate != value:
                return candidate
        return candidate

    def birth_place(self, value: str) -> str:
        others = [c for c in CITIES if c not in value] or CITIES
        city = self._rng.choice(others)
        if value.lower().startswith("г."):
            return f"г. {city}"
        if value.lower().startswith("город"):
            return f"город {city}"
        return f"{city}, Россия" if "россия" in value.lower() else city

    def address(self, value: str) -> str:
        city = self._rng.choice(CITIES)
        street = self._rng.choice(STREETS)
        house = self._rng.randint(1, 250)
        flat = self._rng.randint(1, 500)
        index = self._rng.randint(100000, 699999)
        return f"{index}, г. {city}, ул. {street}, д. {house}, кв. {flat}"

    def short_code(self, value: str, length: int) -> str:
        digits_only = re.sub(r"\D", "", value)
        n = len(digits_only) or length
        for _ in range(10):
            candidate = "".join(str(self._rng.randint(0, 9)) for _ in range(n))
            if candidate != digits_only:
                return candidate
        return candidate

    def generic(self, pii_type: PIIType, value: str) -> str:
        dispatch = {
            PIIType.BIRTH_DATE: self.date_like,
            PIIType.PASSPORT_ISSUE_DATE: self.date_like,
            PIIType.PASSPORT: self.passport_number,
            PIIType.DRIVER_LICENSE: self.driver_license,
            PIIType.DEPARTMENT_CODE: self.department_code,
            PIIType.CITIZENSHIP: self.citizenship,
            PIIType.PASSPORT_ISSUER: self.passport_issuer,
            PIIType.BIRTH_PLACE: self.birth_place,
            PIIType.ADDRESS: self.address,
            PIIType.EMAIL: self.email,
            PIIType.PHONE: self.phone,
            PIIType.INN: self.inn,
            PIIType.CARD_NUMBER: self.card_number,
            PIIType.CVV: lambda v: self.short_code(v, 3),
            PIIType.PIN: lambda v: self.short_code(v, 4),
        }
        handler = dispatch.get(pii_type)
        if handler is None:
            raise KeyError(f"no synthetic generator for {pii_type}")
        return handler(value)


def _inn10_checksum(digits9: list[int]) -> int:
    weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    return sum(a * b for a, b in zip(digits9, weights, strict=False)) % 11 % 10


def _inn12_checks(digits10: list[int]) -> tuple[int, int]:
    w11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    d11 = sum(a * b for a, b in zip(digits10, w11, strict=False)) % 11 % 10
    w12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    d12 = sum(a * b for a, b in zip([*digits10, d11], w12, strict=False)) % 11 % 10
    return d11, d12


def _luhn_check_digit(prefix: str) -> str:
    digits = [int(x) for x in prefix] + [0]
    parity = len(digits) % 2
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((-total) % 10)
