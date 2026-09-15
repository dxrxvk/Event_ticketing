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


def format_minor_units(cents, currency):
    """500, 'EUR' -> 'EUR 5.00'.

    Dot decimal on purpose, unlike format_ars: this figure is typed into Revolut, which
    shows amounts that way, not into an Argentine bank.
    """
    units, remainder = divmod(int(cents), 100)
    # strip(): a tag set before its currency should not render a leading space.
    return f'{currency.upper()} {units:,}.{remainder:02d}'.strip()
