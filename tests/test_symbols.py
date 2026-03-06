from shared.symbols import SymbolConfig, get_symbols

def test_default_symbols():
    symbols = get_symbols()
    assert "BTCUSDT" in [s.symbol for s in symbols]

def test_symbol_config():
    s = SymbolConfig(symbol="ETHUSDT", leverage=25, ws_stream="ethusdt@kline_1s")
    assert s.symbol == "ETHUSDT"
    assert s.leverage == 25
    assert s.ws_stream == "ethusdt@kline_1s"

def test_symbol_from_env(monkeypatch):
    monkeypatch.setenv("TRADE_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
    symbols = get_symbols()
    assert len(symbols) == 3
    assert symbols[1].symbol == "ETHUSDT"
