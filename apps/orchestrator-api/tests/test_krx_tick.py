from ml.krx_tick import ceil_to_tick, floor_to_tick, round_to_tick, tick_size


def test_krx_stock_tick_table_by_market():
    assert [tick_size(price, "KOSPI") for price in (999, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000)] == [1, 5, 10, 50, 100, 500, 1_000]
    assert [tick_size(price, "KOSDAQ") for price in (999, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000)] == [1, 5, 10, 50, 100, 100, 100]


def test_tick_rounding_is_executable_at_price_band_boundaries():
    assert floor_to_tick(99_999, "KOSPI") == 99_900
    assert ceil_to_tick(99_999, "KOSPI") == 100_000
    assert round_to_tick(100_249, "KOSPI") == 100_000
    assert round_to_tick(100_250, "KOSPI") == 100_500
    assert round_to_tick(100_249, "KOSDAQ") == 100_200


def test_exchange_traded_products_keep_their_fixed_tick():
    assert tick_size(300_000, "KOSPI", "ETF") == 5
    assert round_to_tick(70_003, "KOSPI", "ETN") == 70_005
