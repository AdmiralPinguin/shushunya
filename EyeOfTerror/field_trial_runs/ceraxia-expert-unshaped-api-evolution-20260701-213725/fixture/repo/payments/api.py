import warnings

def calculate_total(gross, fee=0, *, service_fee=None):
    if service_fee is None:
        service_fee = fee
        if fee != 0:
            warnings.warn('fee is deprecated; use service_fee', DeprecationWarning, stacklevel=2)
    return gross - service_fee
