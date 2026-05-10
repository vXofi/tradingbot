#!/usr/bin/env python3
"""
Setup sanity check for the trading bot.
Run with: python setup_check.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

errors = 0


def ok(msg: str):
    print(f"{PASS} {msg}")


def fail(msg: str):
    global errors
    errors += 1
    print(f"{FAIL} {msg}")


def warn(msg: str):
    print(f"{WARN} {msg}")


# ── 1. Python version ─────────────────────────────────────────

if sys.version_info >= (3, 10):
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
else:
    fail(f"Python {sys.version_info.major}.{sys.version_info.minor} — requires 3.10+")


# ── 2. Dependencies ───────────────────────────────────────────

required = [
    ("dotenv", "python-dotenv"),
    ("t_tech", "t-tech-investments"),
    ("aiohttp", "aiohttp"),
    ("feedparser", "feedparser"),
    ("rich", "rich"),
]

for module, pkg in required:
    try:
        __import__(module)
        ok(f"{pkg} installed")
    except ImportError:
        fail(f"{pkg} not installed — run: pip install -r requirements.txt")


# ── 3. .env file ──────────────────────────────────────────────

env_path = ROOT / ".env"
if env_path.exists():
    ok(".env found")
else:
    fail(".env not found — copy .env.example to .env and fill in your token")

try:
    from dotenv import load_dotenv
    load_dotenv(env_path)
except Exception:
    pass


# ── 4. TOKEN_TINKOFF ──────────────────────────────────────────

token = os.getenv("TOKEN_TINKOFF", "").strip()
if not token:
    fail("TOKEN_TINKOFF not set in .env")
elif not token.startswith("t."):
    warn("TOKEN_TINKOFF doesn't start with 't.' — may be invalid")
else:
    ok("TOKEN_TINKOFF set")


# ── 5. Tinkoff API connection ─────────────────────────────────

if token:
    try:
        from t_tech.invest import Client

        with Client(token) as client:
            use_sandbox = os.getenv("USE_SANDBOX", "true").lower() == "true"

            if use_sandbox:
                accounts = client.sandbox.get_sandbox_accounts()
                ok("Tinkoff API connection (sandbox)")

                if accounts.accounts:
                    acct = accounts.accounts[0]
                    ok(f"Sandbox account found: {acct.id}")

                    # Check balance
                    try:
                        portfolio = client.sandbox.get_sandbox_portfolio(
                            account_id=acct.id
                        )
                        total = portfolio.total_amount_currencies
                        if total:
                            rub = next(
                                (a for a in [total] if getattr(a, "currency", "") == "rub"),
                                None,
                            )
                            if rub:
                                balance = rub.units + rub.nano / 1e9
                                if balance > 0:
                                    ok(f"Account balance: {balance:,.0f} RUB")
                                else:
                                    warn("Account balance is 0 RUB — bot will deposit on first start")
                            else:
                                # Try direct total
                                ok("Account portfolio accessible")
                    except Exception:
                        ok("Sandbox account accessible")
                else:
                    warn("No sandbox accounts yet — one will be created on first bot start")
            else:
                accounts = client.users.get_accounts()
                ok("Tinkoff API connection (production)")
                if accounts.accounts:
                    ok(f"Production account found: {accounts.accounts[0].id}")
                else:
                    fail("No production accounts found")

    except Exception as e:
        err = str(e)
        if "DNS" in err or "UNAVAILABLE" in err or "network" in err.lower():
            warn(f"Tinkoff API unreachable (network/DNS) — check your internet connection")
        elif "UNAUTHENTICATED" in err or "401" in err:
            fail("TOKEN_TINKOFF is invalid or expired — generate a new token")
        else:
            fail(f"Tinkoff API error: {e}")


# ── 6. Gemini API key (optional) ─────────────────────────────

gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
if not gemini_key:
    warn("GEMINI_API_KEY not set — LLM fallback in news parsing disabled (optional)")
else:
    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        # Just init the model — don't make an API call
        genai.GenerativeModel("gemini-1.5-flash")
        ok("GEMINI_API_KEY set and SDK loaded")
    except ImportError:
        warn("GEMINI_API_KEY set but google-generativeai not installed")
    except Exception as e:
        warn(f"GEMINI_API_KEY set but SDK error: {e}")


# ── 7. Data files ─────────────────────────────────────────────

data_files = [
    ("whitelist.json", "Tradeable instruments"),
    ("keywords.json", "NLP keyword triggers"),
    ("entities.json", "Ticker entity mappings"),
    ("sectors.json", "Sector-to-ticker mappings"),
    ("rss_feeds.json", "RSS news feed URLs"),
]

for fname, desc in data_files:
    path = ROOT / "data" / fname
    if path.exists():
        ok(f"{fname} ({desc})")
    else:
        fail(f"{fname} missing — {desc}")


# ── 8. Whitelist count ────────────────────────────────────────

try:
    import json
    with open(ROOT / "data" / "whitelist.json") as f:
        wl = json.load(f)
    count = len(wl.get("instruments", []))
    if count > 0:
        ok(f"Whitelist: {count} instruments")
    else:
        warn("Whitelist is empty")
except Exception:
    pass  # already reported above


# ── Summary ───────────────────────────────────────────────────

print()
if errors == 0:
    print("🚀 All checks passed — ready to run: python main.py")
else:
    print(f"Fix the {errors} error(s) above, then run this check again.")
    sys.exit(1)
