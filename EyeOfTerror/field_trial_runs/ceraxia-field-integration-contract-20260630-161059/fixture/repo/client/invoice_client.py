from api.invoice_service import calculate_invoice

def invoice_total(gross, fee):
    return calculate_invoice({'gross': gross, 'fee': fee})['net_total']
