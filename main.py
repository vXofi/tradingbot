#!/usr/bin/env python3
"""
Event-Driven Trading Bot — single entry point.

Usage:
    python main.py              # Start bot + RSS + live dashboard
    python main.py --no-rss     # Start without RSS listener
    python main.py --sandbox    # Run legacy sandbox debug script
"""

import sys


def main():
    if "--sandbox" in sys.argv:
        from _sandbox_debug import run_bot
        run_bot()
        return

    if "--backtest" in sys.argv:
        import asyncio
        from backtest.runner import run_backtest_cli
        try:
            asyncio.run(run_backtest_cli())
        except KeyboardInterrupt:
            pass
        return

    import asyncio
    from dashboard import main as dashboard_main

    try:
        asyncio.run(dashboard_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
