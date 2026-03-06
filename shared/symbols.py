import os
from dataclasses import dataclass

LEVERAGE_DEFAULTS = {
    "BTCUSDT": 50, "ETHUSDT": 30, "SOLUSDT": 20,
    "BNBUSDT": 20, "XRPUSDT": 15, "DOGEUSDT": 10,
}

@dataclass
class SymbolConfig:
    symbol: str
    leverage: int = 50
    ws_stream: str = ""

    def __post_init__(self):
        if not self.ws_stream:
            self.ws_stream = f"{self.symbol.lower()}@kline_1s"
        if self.leverage == 50 and self.symbol in LEVERAGE_DEFAULTS:
            self.leverage = LEVERAGE_DEFAULTS[self.symbol]

def get_symbols() -> list[SymbolConfig]:
    env = os.environ.get("TRADE_SYMBOLS", "BTCUSDT")
    symbols = [s.strip() for s in env.split(",") if s.strip()]
    return [SymbolConfig(symbol=s) for s in symbols]
