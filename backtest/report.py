"""
Backtest report — statistics and Rich terminal display.
"""

from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.table import Table

from bot.config import TradingConfig
from bot.models import Sentiment

from .simulator import TradeResult


class BacktestReport:
    """Compute and display backtest statistics."""

    def __init__(
        self,
        results: list[TradeResult],
        config: Optional[TradingConfig] = None,
    ):
        self.results = results
        self.config = config or TradingConfig()

    def summary(self) -> dict:
        """Return a stats dict."""
        if not self.results:
            return {
                "total": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0,
                "best_trade": 0.0, "worst_trade": 0.0,
                "max_drawdown": 0.0, "profit_factor": 0.0,
                "avg_duration_sec": 0.0,
            }

        pnls = [r.pnl for r in self.results]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0

        return {
            "total": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(pnls) * 100,
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(pnls),
            "best_trade": max(pnls),
            "worst_trade": min(pnls),
            "max_drawdown": self._max_drawdown(pnls),
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "avg_duration_sec": sum(r.duration_sec for r in self.results) / len(self.results),
        }

    def by_exit_reason(self) -> dict[str, dict]:
        """PnL breakdown by exit reason."""
        buckets: dict[str, list[float]] = {}
        for r in self.results:
            buckets.setdefault(r.exit_reason, []).append(r.pnl)
        return {
            reason: {
                "count": len(pnls),
                "total_pnl": sum(pnls),
                "avg_pnl": sum(pnls) / len(pnls),
                "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) * 100,
            }
            for reason, pnls in sorted(buckets.items())
        }

    def equity_curve(self) -> list[tuple[datetime, float]]:
        """Running cumulative PnL series."""
        curve: list[tuple[datetime, float]] = []
        cum = 0.0
        for r in self.results:
            cum += r.pnl
            curve.append((r.exit_time, cum))
        return curve

    @staticmethod
    def _max_drawdown(pnls: list[float]) -> float:
        """Peak-to-trough drawdown of cumulative PnL."""
        peak = 0.0
        cum = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        return max_dd

    # ── Rich terminal output ──────────────────────────────────

    def print_report(self, console: Optional[Console] = None):
        """Print a formatted report to the terminal."""
        console = console or Console()
        stats = self.summary()

        console.print()
        console.rule("[bold]BACKTEST REPORT[/]")
        console.print()

        # Summary stats
        tbl = Table(title="Summary", show_header=False, box=None, padding=(0, 2))
        tbl.add_column(style="bold")
        tbl.add_column(justify="right")

        pnl_style = "green" if stats["total_pnl"] >= 0 else "red"
        tbl.add_row("Total trades", str(stats["total"]))
        tbl.add_row("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
        tbl.add_row("Win rate", f"{stats['win_rate']:.1f}%")
        tbl.add_row("Total PnL", f"[{pnl_style}]{stats['total_pnl']:+.2f} RUB[/]")
        tbl.add_row("Avg PnL", f"{stats['avg_pnl']:+.2f} RUB")
        tbl.add_row("Best trade", f"[green]{stats['best_trade']:+.2f}[/]")
        tbl.add_row("Worst trade", f"[red]{stats['worst_trade']:+.2f}[/]")
        tbl.add_row("Max drawdown", f"[red]{stats['max_drawdown']:.2f}[/]")
        tbl.add_row("Profit factor", f"{stats['profit_factor']:.2f}")
        tbl.add_row("Avg duration", f"{stats['avg_duration_sec'] / 60:.1f} min")
        console.print(tbl)

        # By exit reason
        console.print()
        reasons = self.by_exit_reason()
        if reasons:
            rtbl = Table(title="By Exit Reason")
            rtbl.add_column("Reason")
            rtbl.add_column("Count", justify="right")
            rtbl.add_column("PnL", justify="right")
            rtbl.add_column("Win Rate", justify="right")
            for reason, data in reasons.items():
                s = "green" if data["total_pnl"] >= 0 else "red"
                rtbl.add_row(
                    reason, str(data["count"]),
                    f"[{s}]{data['total_pnl']:+.2f}[/]",
                    f"{data['win_rate']:.0f}%",
                )
            console.print(rtbl)

        # Per-trade table
        console.print()
        ttbl = Table(title="Trade Log")
        ttbl.add_column("Ticker")
        ttbl.add_column("Dir")
        ttbl.add_column("Entry", justify="right")
        ttbl.add_column("Exit", justify="right")
        ttbl.add_column("PnL", justify="right")
        ttbl.add_column("Reason")
        ttbl.add_column("Duration")

        for r in self.results:
            s = "green" if r.pnl >= 0 else "red"
            dur = f"{r.duration_sec / 60:.1f}m"
            ttbl.add_row(
                r.ticker,
                r.direction.value[:4],
                f"{r.entry_price:.2f}",
                f"{r.exit_price:.2f}",
                f"[{s}]{r.pnl:+.2f}[/]",
                r.exit_reason,
                dur,
            )
        console.print(ttbl)
        console.print()
