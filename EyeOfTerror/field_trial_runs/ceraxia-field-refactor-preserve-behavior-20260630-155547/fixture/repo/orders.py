from common.calculations import net_amount


def order_total(gross, fee):
    return net_amount(gross, fee)
