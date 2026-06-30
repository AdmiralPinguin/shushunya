from common.calculations import net_amount


def refund_total(gross, fee):
    return net_amount(gross, fee)
