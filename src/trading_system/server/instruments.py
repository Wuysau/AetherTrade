"""Explicit initial Binance spot catalog, not a full bStocks directory.

bStock base assets and underlying symbols are verified mappings; never infer
asset class from a ticker suffix. PAPER does not simulate corporate actions.
"""

_CATALOG = (
    ("BTCUSDT", "Bitcoin", "CRYPTO", "BTC", None),
    ("ETHUSDT", "Ethereum", "CRYPTO", "ETH", None),
    ("SOLUSDT", "Solana", "CRYPTO", "SOL", None),
    ("TSLABUSDT", "Tesla", "BSTOCK", "TSLAB", "TSLA"),
    ("NVDABUSDT", "NVIDIA", "BSTOCK", "NVDAB", "NVDA"),
    ("AAPLBUSDT", "Apple", "BSTOCK", "AAPLB", "AAPL"),
)
ALL_SYMBOLS = tuple(item[0] for item in _CATALOG)
CRYPTO_SYMBOLS = tuple(item[0] for item in _CATALOG if item[2] == "CRYPTO")
BSTOCK_SYMBOLS = tuple(item[0] for item in _CATALOG if item[2] == "BSTOCK")


def instruments() -> list[dict]:
    """Return detached public metadata for the supported initial catalog."""
    return [
        {"symbol": symbol, "name": name, "asset_class": asset_class,
         "base_asset": base, "quote_asset": "USDT", "underlying_symbol": underlying}
        for symbol, name, asset_class, base, underlying in _CATALOG
    ]
