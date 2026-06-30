RATES = {'standard': 0.20, 'reduced': 0.05}

def tax_for(amount, category='standard'):
    return amount * RATES[category]
