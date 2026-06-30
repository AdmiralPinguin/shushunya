from tax.rates import tax_for

def invoice_tax(amount, category='standard'):
    return tax_for(amount, category)
