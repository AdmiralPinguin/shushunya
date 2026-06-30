from billing.public_api import calculate_total

def client_total(gross, fee):
    return calculate_total(gross, fee)
