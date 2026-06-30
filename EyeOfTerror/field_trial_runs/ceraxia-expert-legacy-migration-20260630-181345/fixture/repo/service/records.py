def normalize_record(record):
    if 'total_amount' in record:
        value = record['total_amount']
    elif 'amount' in record:
        value = record['amount']
    else:
        raise KeyError('total_amount')
    return {'id': record['id'], 'total_amount': value}

def serialize_record(record):
    normalized = normalize_record(record)
    return {'id': normalized['id'], 'total_amount': normalized['total_amount']}
