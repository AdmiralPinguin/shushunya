from payments.api import calculate_total

def client_total(gross, service_fee):
    return calculate_total(gross, service_fee=service_fee)
