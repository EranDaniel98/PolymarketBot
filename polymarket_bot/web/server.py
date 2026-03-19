"""Lightweight web dashboard for PolymarketBot."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="PolymarketBot Dashboard")


def get_db():
    return app.state.db


def get_exit_mgr():
    return app.state.exit_mgr


@app.get("/api/stats")
async def stats():
    db = get_db()
    exit_mgr = get_exit_mgr()
    return {
        "daily_pnl": await db.get_daily_pnl(),
        "total_pnl": await db.get_total_pnl(),
        "exposure": await db.get_total_exposure(),
        "trade_count": await db.get_trade_count(),
        "win_rate": await db.get_win_rate(),
        "open_positions": len(exit_mgr._positions) if exit_mgr else 0,
    }


@app.get("/api/positions")
async def positions():
    db = get_db()
    return await db.load_positions()


@app.get("/api/trades")
async def trades():
    db = get_db()
    return (await db.get_trades())[:50]


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text())
