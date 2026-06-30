def schedule_order(items):
    return sorted(items, key=lambda item: (item['priority'], item['id']))
