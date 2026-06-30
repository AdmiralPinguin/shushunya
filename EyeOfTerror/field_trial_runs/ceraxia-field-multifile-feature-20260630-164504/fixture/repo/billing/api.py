from billing.discounts import apply_discount

def discounted_total(price, percent):
    return apply_discount(price, percent)
