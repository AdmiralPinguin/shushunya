def calculate_invoice(payload):
    gross = payload['gross']
    fee = payload['fee']
    return {'net_total': gross - fee}
