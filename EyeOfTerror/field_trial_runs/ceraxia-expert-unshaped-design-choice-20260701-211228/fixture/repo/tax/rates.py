RATES = {'standard': 0.2, 'reduced': 0.05}

def tax_for(amount, category='standard'):
    try:
        rate = RATES[category]
    except KeyError as exc:
        raise ValueError(f'unknown tax category: {category}') from exc
    return amount * rate
