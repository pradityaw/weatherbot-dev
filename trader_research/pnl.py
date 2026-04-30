from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenPosition:
    yes_shares: float = 0.0
    no_shares: float = 0.0
    cash_flow: float = 0.0  # + received USDC, - paid USDC (trading only)
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    n_trades: int = 0

    def apply_trade(self, side: str, outcome: str, price: float, size: float) -> None:
        s = str(side or "").upper()
        o = str(outcome or "").lower()
        if o not in {"yes", "no"}:
            return
        sign = 1.0 if s == "BUY" else -1.0 if s == "SELL" else 0.0
        if sign == 0.0:
            return
        notional = float(price) * float(size)
        if o == "yes":
            self.yes_shares += sign * float(size)
        else:
            self.no_shares += sign * float(size)
        # BUY: pay USDC; SELL: receive USDC
        if s == "BUY":
            self.cash_flow -= notional
            self.buy_notional += notional
        else:
            self.cash_flow += notional
            self.sell_notional += notional
        self.n_trades += 1

    def resolved_pnl(self, yes_won: bool) -> float:
        py = 1.0 if yes_won else 0.0
        pn = 1.0 - py
        terminal = self.yes_shares * py + self.no_shares * pn
        return self.cash_flow + terminal


@dataclass
class WalletStats:
    address: str
    total_pnl: float = 0.0
    markets_traded: int = 0
    winning_markets: int = 0
    total_buy_notional: float = 0.0
    market_pnls: list[float] = field(default_factory=list)

    @property
    def roi(self) -> float:
        if self.total_buy_notional <= 0:
            return 0.0
        return self.total_pnl / self.total_buy_notional

    @property
    def win_rate(self) -> float:
        if self.markets_traded <= 0:
            return 0.0
        return self.winning_markets / self.markets_traded


def pnl_by_wallet_for_market(
    trades: list[dict[str, Any]],
    yes_won: bool,
) -> dict[str, float]:
    """Realized PnL per proxy wallet for one resolved binary market."""
    by_wallet: dict[str, TokenPosition] = {}
    sorted_trades = sorted(
        trades,
        key=lambda t: (int(t.get("timestamp") or 0), str(t.get("transactionHash", ""))),
    )
    for t in sorted_trades:
        w = str(t.get("proxyWallet", "") or "").lower()
        if not w:
            continue
        pos = by_wallet.setdefault(w, TokenPosition())
        pos.apply_trade(
            str(t.get("side", "")),
            str(t.get("outcome", "")),
            float(t.get("price") or 0.0),
            float(t.get("size") or 0.0),
        )
    out: dict[str, float] = {}
    for w, pos in by_wallet.items():
        out[w] = round(pos.resolved_pnl(yes_won), 6)
    return out


def position_for_market(trades: list[dict[str, Any]], wallet: str) -> TokenPosition:
    w = wallet.lower()
    pos = TokenPosition()
    sorted_trades = sorted(
        trades,
        key=lambda t: (int(t.get("timestamp") or 0), str(t.get("transactionHash", ""))),
    )
    for t in sorted_trades:
        if str(t.get("proxyWallet", "") or "").lower() != w:
            continue
        pos.apply_trade(
            str(t.get("side", "")),
            str(t.get("outcome", "")),
            float(t.get("price") or 0.0),
            float(t.get("size") or 0.0),
        )
    return pos
