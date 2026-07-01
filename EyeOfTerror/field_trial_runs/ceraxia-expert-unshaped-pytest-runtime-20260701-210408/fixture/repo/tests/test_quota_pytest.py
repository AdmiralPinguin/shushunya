from quota import max_daily_exports

def test_pytest_limit():
    assert max_daily_exports() == 7
