from parser import parse_amount

def handle_payload(payload):
    return {'amount': parse_amount(payload['amount'])}
