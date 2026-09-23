from __future__ import annotations


def luhn_valid(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def inn_valid(digits: str) -> bool:
    if not digits.isdigit():
        return False
    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        check = sum(int(d) * w for d, w in zip(digits[:9], weights, strict=False)) % 11 % 10
        return check == int(digits[9])
    if len(digits) == 12:
        w11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        w12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        d11 = sum(int(d) * w for d, w in zip(digits[:10], w11, strict=False)) % 11 % 10
        d12 = sum(int(d) * w for d, w in zip(digits[:10] + str(d11), w12, strict=False)) % 11 % 10
        return d11 == int(digits[10]) and d12 == int(digits[11])
    return False
