"""Money formatting.

Amounts are integer cents everywhere in this project. These helpers exist so the
Argentine display format lives in exactly one place: the UI copy is English, but the
amount is still rendered `5.000` (dot as thousands separator) because it is typed into an
Argentine bank's amount field. Do not "fix" it to `5,000` to match the English copy.
"""


def format_ars(cents, with_cents=False):
    """500000 -> '5.000'. With with_cents=True, '5.000,00'."""
    pesos, remainder = divmod(int(cents), 100)
    grouped = f'{pesos:,}'.replace(',', '.')
    if with_cents:
        return f'{grouped},{remainder:02d}'
    return grouped
