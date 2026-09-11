"""Pluto command line."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from pluto.core.demo import demo_portfolio
from pluto.core.holdings import compute_holdings
from pluto.core.model import Portfolio, PortfolioError, Transaction, TxType
from pluto.store import paths
from pluto.store.versions import StoreError, VersionStore

app = typer.Typer(help="Pluto: a local, AI-first portfolio manager.", no_args_is_help=True)
console = Console()
err = Console(stderr=True, style="bold red")

PortfolioOpt = Annotated[
    str | None, typer.Option("--portfolio", "-p", help="Portfolio name (default: configured)")
]


def _store(name: str | None) -> VersionStore:
    return VersionStore(paths.portfolio_dir(name or paths.default_portfolio()))


def _load(name: str | None) -> tuple[VersionStore, Portfolio]:
    store = _store(name)
    try:
        return store, store.load()
    except StoreError as e:
        err.print(f"{e}. Run `pluto demo` or `pluto init <name>` first.")
        raise typer.Exit(1) from None


def fmt(x: Decimal | None, places: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:,.{places}f}"


# --- portfolio lifecycle ---------------------------------------------------------------


@app.command()
def demo(name: str = "demo", force: bool = typer.Option(False, help="Overwrite if it exists")):
    """Create the demo portfolio and make it the default."""
    store = VersionStore(paths.portfolio_dir(name))
    if store.exists() and not force:
        err.print(f"portfolio {name!r} already exists (use --force to recreate)")
        raise typer.Exit(1)
    info = store.commit(demo_portfolio(name), "Demo portfolio")
    paths.write_config({**paths.read_config(), "portfolio": name})
    console.print(
        f"Created demo portfolio [bold]{name}[/] (version {info.version}) at {store.root}"
    )


@app.command()
def init(name: str, base_currency: str = "EUR"):
    """Create an empty portfolio."""
    store = VersionStore(paths.portfolio_dir(name))
    if store.exists():
        err.print(f"portfolio {name!r} already exists")
        raise typer.Exit(1)
    store.commit(Portfolio(name=name, base_currency=base_currency.upper()), "Empty portfolio")
    paths.write_config({**paths.read_config(), "portfolio": name})
    console.print(f"Created empty portfolio [bold]{name}[/] ({base_currency.upper()})")


@app.command("use")
def use_portfolio(name: str):
    """Set the default portfolio."""
    if name not in paths.list_portfolios():
        err.print(f"no portfolio named {name!r}; known: {paths.list_portfolios()}")
        raise typer.Exit(1)
    paths.write_config({**paths.read_config(), "portfolio": name})
    console.print(f"Default portfolio is now [bold]{name}[/]")


@app.command()
def delete(name: str, yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation")):
    """Move a portfolio to ~/.pluto/trash (nothing is erased)."""
    if name not in paths.list_portfolios():
        err.print(f"no portfolio named {name!r}; known: {paths.list_portfolios()}")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"Move portfolio {name!r} to the trash?"):
        raise typer.Exit(0)
    try:
        dest = paths.delete_portfolio(name)
    except ValueError as e:
        err.print(str(e))
        raise typer.Exit(1) from None
    console.print(f"Moved to {dest}. Default portfolio is now [bold]{paths.default_portfolio()}[/]")


@app.command("list")
def list_cmd():
    """List portfolios."""
    default = paths.default_portfolio()
    for n in paths.list_portfolios():
        console.print(f"{'*' if n == default else ' '} {n}")


# --- reading ---------------------------------------------------------------------------


@app.command()
def show(portfolio: PortfolioOpt = None):
    """Holdings (quantities and cost, no market data)."""
    store, p = _load(portfolio)
    h = compute_holdings(p)
    t = Table(title=f"{p.name} · version {store.head()} · base {p.base_currency}")
    for col in ("Instrument", "ISIN", "Qty", "Avg cost", "Cost basis", "Ccy", "Realized"):
        t.add_column(col, justify="right" if col not in ("Instrument", "ISIN", "Ccy") else "left")
    for pos in h.open_positions():
        ins = p.instrument(pos.instrument_id)
        t.add_row(
            ins.name,
            ins.isin or "",
            fmt(pos.quantity, 4),
            fmt(pos.avg_cost),
            fmt(pos.cost_basis),
            ins.currency,
            fmt(pos.realized_pnl),
        )
    console.print(t)
    if p.tracks_cash():
        cash = ", ".join(f"{fmt(v)} {k}" for k, v in h.cash.items() if v)
        console.print(f"Cash: {cash or '0'}")
    else:
        console.print("[dim]Cash not tracked (record a deposit to start, or `pluto cash on`)[/]")


@app.command()
def transactions(portfolio: PortfolioOpt = None, last: int = 0):
    """List transactions."""
    _, p = _load(portfolio)
    t = Table()
    for col in ("Id", "Date", "Type", "Instrument", "Qty", "Price", "Amount", "Ccy", "Fees", "Src"):
        t.add_column(col)
    rows = p.transactions[-last:] if last else p.transactions
    for tx in rows:
        name = p.instruments[tx.instrument_id].name if tx.instrument_id else ""
        t.add_row(
            tx.id,
            str(tx.date),
            tx.type.value,
            name,
            fmt(tx.quantity, 4),
            fmt(tx.price),
            fmt(tx.amount),
            tx.currency,
            fmt(tx.fees),
            tx.source,
        )
    console.print(t)


@app.command()
def instruments(portfolio: PortfolioOpt = None):
    """List instruments and their listings."""
    _, p = _load(portfolio)
    t = Table()
    for col in ("Id", "Name", "Type", "Class", "Ccy", "Listings"):
        t.add_column(col)
    for ins in p.instruments.values():
        t.add_row(
            ins.id,
            ins.name,
            ins.asset_type.value,
            ins.asset_class.value,
            ins.currency,
            ", ".join(ins.symbols_in_order()),
        )
    console.print(t)


# --- writing ---------------------------------------------------------------------------


def _dec(s: str, what: str) -> Decimal:
    try:
        return Decimal(s)
    except InvalidOperation:
        err.print(f"invalid {what}: {s!r}")
        raise typer.Exit(2) from None


@app.command()
def add(
    kind: Annotated[TxType, typer.Argument(help="buy|sell|dividend|fee|deposit|withdrawal")],
    instrument: Annotated[str | None, typer.Option(help="ISIN, id or symbol")] = None,
    quantity: str | None = None,
    price: str | None = None,
    amount: str | None = None,
    currency: str | None = None,
    fees: str = "0",
    on: Annotated[str | None, typer.Option(help="Date YYYY-MM-DD (default today)")] = None,
    note: str = "",
    portfolio: PortfolioOpt = None,
):
    """Append a transaction (creates a new version)."""
    store, p = _load(portfolio)
    ins = None
    if instrument:
        ins = p.find_instrument(instrument)
        if ins is None:
            err.print(f"unknown instrument {instrument!r}; add it first (see `pluto instruments`)")
            raise typer.Exit(1)
    try:
        tx = Transaction(
            date=date.fromisoformat(on) if on else date.today(),
            type=kind,
            instrument_id=ins.id if ins else None,
            quantity=_dec(quantity, "quantity") if quantity else None,
            price=_dec(price, "price") if price else None,
            amount=_dec(amount, "amount") if amount else None,
            currency=(currency or (ins.currency if ins else p.base_currency)).upper(),
            fees=_dec(fees, "fees"),
            note=note,
            source="cli",
        )
        p.add_transaction(tx)
    except (ValueError, PortfolioError) as e:
        err.print(str(e))
        raise typer.Exit(1) from None
    info = store.commit(p, tx.describe(ins.name if ins else None))
    console.print(f"Added {tx.describe(ins.name if ins else None)} → version {info.version}")


@app.command()
def remove(tx_id: str, portfolio: PortfolioOpt = None):
    """Remove a transaction by id (creates a new version)."""
    store, p = _load(portfolio)
    try:
        tx = p.remove_transaction(tx_id)
    except PortfolioError as e:
        err.print(str(e))
        raise typer.Exit(1) from None
    info = store.commit(p, f"Remove {tx.describe()}")
    console.print(f"Removed {tx.id} → version {info.version}")


@app.command()
def cash(
    mode: Annotated[str, typer.Argument(help="on | off | auto")],
    portfolio: PortfolioOpt = None,
):
    """Whether cash balances are tracked. auto = once a deposit/withdrawal exists."""
    if mode not in ("on", "off", "auto"):
        err.print("mode must be on, off or auto")
        raise typer.Exit(2)
    store, p = _load(portfolio)
    p.track_cash = {"on": True, "off": False, "auto": None}[mode]
    info = store.commit(p, f"Cash tracking {mode}")
    console.print(
        f"Cash tracking: {mode} (tracked now: {p.tracks_cash()}) → version {info.version}"
    )


@app.command()
def listing(
    instrument: Annotated[str, typer.Argument(help="ISIN, symbol or id")],
    symbol: Annotated[str, typer.Argument(help="Yahoo symbol to quote from, e.g. SNPS")],
    portfolio: PortfolioOpt = None,
):
    """Quote an instrument from a different listing."""
    from pluto.chat.tools import ChatContext, ToolRegistry

    store, _ = _load(portfolio)
    ctx = ChatContext.create(store)
    ctx.source = "cli"

    async def go():
        try:
            args = {"instrument": instrument, "symbol": symbol}
            return await ToolRegistry(ctx).call("set_instrument_listing", args)
        finally:
            await ctx.close()

    res = _run(go())
    if not res.ok:
        err.print(str(res.data.get("error")))
        raise typer.Exit(1)
    d = res.data
    q = d.get("quote") or {}
    console.print(
        f"{d['instrument']['name']}: quoting from {d['instrument']['symbols'][0]} "
        f"→ {q.get('price')} {q.get('currency')} ({q.get('source')}) · version {d['version']}"
    )
    if d.get("note"):
        console.print(f"[dim]{d['note']}[/]")


def _tool(portfolio: str | None, name: str, args: dict[str, object]):
    from pluto.chat.tools import ChatContext, ToolRegistry

    store, _ = _load(portfolio)
    ctx = ChatContext.create(store)
    ctx.source = "cli"

    async def go():
        try:
            return await ToolRegistry(ctx).call(name, args)
        finally:
            await ctx.close()

    res = _run(go())
    if not res.ok:
        err.print(str(res.data.get("error")))
        raise typer.Exit(1)
    return res.data


@app.command()
def perf(
    period: str = typer.Option("1y", help="1m, 3m, 6m, ytd, 1y, 3y, 5y, all"),
    benchmark: str | None = typer.Option(None, help="Yahoo symbol, default world equity ETF"),
    portfolio: PortfolioOpt = None,
):
    """Performance and risk: actual history and today's composition backtested."""
    d = _tool(portfolio, "get_performance", {"period": period, "benchmark": benchmark})
    ccy = d["base_currency"]

    def block(title: str, m: dict) -> None:
        t = Table(title=f"{title} · {m['start']} → {m['end']}")
        t.add_column("Metric")
        t.add_column("Value", justify="right")
        rows = [
            ("Start value", f"{fmt(Decimal(m['start_value']))} {ccy}"),
            ("End value", f"{fmt(Decimal(m['end_value']))} {ccy}"),
            ("Net flows", f"{fmt(Decimal(m['net_flows']))} {ccy}"),
            ("Gain", f"{fmt(Decimal(m['gain']))} {ccy}"),
            ("Time-weighted return", _pct(m["twr_pct"])),
            ("  annualized", _pct(m["twr_annualized_pct"])),
            ("Money-weighted (annualized)", _pct(m["mwr_annualized_pct"])),
            ("Volatility (annualized)", _pct(m["volatility_pct"])),
            (
                "Max drawdown",
                f"{_pct(m['max_drawdown_pct'])}  {m['drawdown_from'] or ''}"
                f" → {m['drawdown_to'] or ''}",
            ),
        ]
        for k, v in rows:
            t.add_row(k, v)
        console.print(t)
        if m.get("note"):
            console.print(f"[yellow]{m['note']}[/]")

    block("Actual", d["actual"])
    block("Today's composition, backtested", d["composition"])
    if d["benchmark"]:
        b = d["benchmark"]
        console.print(f"Benchmark {b['symbol']}: {_pct(b['twr'])} over the same window")
    ct = Table(title="Contribution (composition, total return)")
    ct.add_column("Instrument")
    ct.add_column("Gain", justify="right")
    ct.add_column("Weight", justify="right")
    for c in d["composition"]["contributions"]:
        ct.add_row(c["name"][:50], f"{fmt(Decimal(c['gain']))} {ccy}", f"{c['weight_pct']}%")
    console.print(ct)
    if d["missing_history"]:
        console.print(f"[red]No price history for: {', '.join(d['missing_history'])}[/]")


def _pct(v: object) -> str:
    return "n/a" if v is None else f"{Decimal(str(v)):+.2f}%"


@app.command()
def classify(
    instrument: Annotated[str, typer.Argument(help="ISIN, symbol or id")],
    asset_class: Annotated[
        str, typer.Argument(help="equity|bond|money_market|commodity|real_estate|multi_asset|other")
    ],
    portfolio: PortfolioOpt = None,
):
    """Set an instrument's asset class explicitly (never overwritten by guesses)."""
    d = _tool(portfolio, "set_asset_class", {"instrument": instrument, "asset_class": asset_class})
    console.print(
        f"{d['instrument']['name']} → {d['instrument']['asset_class']} · version {d['version']}"
    )


@app.command()
def reclassify(portfolio: PortfolioOpt = None):
    """Re-guess asset classes from names for instruments you have not classified yourself."""
    d = _tool(portfolio, "reclassify", {})
    for c in d["changed"]:
        console.print(f"{c['instrument']}: {c['from']} → {c['to']}")
    suffix = f" · version {d['version']}" if d["version"] else " (no change)"
    t = Table(title=f"Asset classes{suffix}")
    t.add_column("Instrument")
    t.add_column("Class")
    for name, cls in d["classes"].items():
        t.add_row(name, cls)
    console.print(t)


@app.command()
def history(portfolio: PortfolioOpt = None):
    """Version history."""
    store, _ = _load(portfolio)
    head = store.head()
    t = Table(title=f"{store.root.name} · HEAD = {head}")
    for col in ("Ver", "When", "Tx", "Message"):
        t.add_column(col)
    for v in store.history():
        mark = "*" if v.version == head else " "
        t.add_row(
            f"{mark}{v.version}",
            v.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            str(v.transactions),
            v.message,
        )
    console.print(t)


@app.command()
def revert(version: int, portfolio: PortfolioOpt = None):
    """Go back to an earlier version (recorded as a new version, nothing is deleted)."""
    store, _ = _load(portfolio)
    try:
        info = store.revert(version)
    except StoreError as e:
        err.print(str(e))
        raise typer.Exit(1) from None
    console.print(f"{info.message} → version {info.version}")


@app.command()
def undo(portfolio: PortfolioOpt = None):
    """Revert to the version before HEAD."""
    store, _ = _load(portfolio)
    head = store.head()
    if head <= 1:
        err.print("nothing to undo")
        raise typer.Exit(1)
    info = store.revert(head - 1)
    console.print(f"{info.message} → version {info.version}")


if __name__ == "__main__":
    app()


# --- market data ---------------------------------------------------------------------

quotes_app = typer.Typer(help="Market data: quotes, FX, diagnostics.")
app.add_typer(quotes_app, name="quotes")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


@app.command()
def value(
    portfolio: PortfolioOpt = None,
    force: bool = typer.Option(False, help="Ignore the quote cache TTL"),
    by: str = typer.Option(
        "instrument", help="Breakdown: instrument|asset_type|asset_class|currency"
    ),
):
    """Intraday valuation and allocation."""
    from pluto.market.service import make_service

    _, p = _load(portfolio)

    async def go():
        svc = make_service()
        async with svc.client:
            return await svc.value(p, force=force), svc

    v, svc = _run(go())
    t = Table(title=f"{p.name} · {v.as_of:%Y-%m-%d %H:%M} · {v.base_currency}")
    for col, just in (
        ("Instrument", "left"),
        ("Qty", "right"),
        ("Price", "right"),
        ("Ccy", "left"),
        ("Value", "right"),
        ("Weight", "right"),
        ("P&L", "right"),
        ("P&L %", "right"),
        ("Day %", "right"),
        ("Quote", "left"),
    ):
        t.add_column(col, justify=just)  # type: ignore[arg-type]
    for vp in v.positions:
        q = vp.quote
        if q is None:
            src = "[red]missing[/]"
        else:
            age = (v.as_of - q.as_of).total_seconds() / 60
            src = f"{q.source} {q.as_of:%H:%M}"
            src = f"[yellow]stale[/] {src}" if q.is_stale else src
            if age > 24 * 60:
                src += f" ({age / 1440:.0f}d old)"
        pnl = vp.unrealized_pnl
        color = "green" if pnl and pnl >= 0 else "red"
        t.add_row(
            vp.instrument.name[:40],
            fmt(vp.position.quantity, 2),
            fmt(q.price) if q else "-",
            q.currency if q else "",
            fmt(vp.market_value),
            f"{vp.weight * 100:.1f}%" if vp.market_value else "-",
            f"[{color}]{fmt(pnl)}[/]" if pnl is not None else "-",
            f"[{color}]{fmt(vp.unrealized_pnl_pct, 1)}%[/]" if pnl is not None else "-",
            fmt(q.change_pct, 2) + "%" if q and q.change_pct is not None else "-",
            src,
        )
    console.print(t)
    console.print(
        f"Cash: {fmt(v.cash_value)} {v.base_currency}   "
        f"[bold]Total: {fmt(v.total_value)} {v.base_currency}[/]"
    )
    for w in v.warnings:
        console.print(f"[yellow]{w}[/]")
    if v.missing:
        console.print(f"[red]No price for: {', '.join(v.missing)}[/]")
    if v.stale:
        console.print(f"[yellow]Stale (from cache): {', '.join(v.stale)}[/]")
    bt = Table(title=f"Allocation by {by}")
    bt.add_column("Slice")
    bt.add_column("Value", justify="right")
    bt.add_column("Weight", justify="right")
    for s in v.breakdown(by):
        bt.add_row(s.label, fmt(s.value), f"{s.weight * 100:.1f}%")
    console.print(bt)
    _print_health(svc)


def _print_health(svc) -> None:
    bad = [h for h in svc.health.values() if h.total_failures]
    for h in bad:
        console.print(
            f"[yellow]{h.name}: {h.total_failures}/{h.total_calls} failures, "
            f"last: {h.last_error}[/]"
        )


@quotes_app.command("doctor")
def quotes_doctor(portfolio: PortfolioOpt = None):
    """Check every instrument against every quote provider."""
    from pluto.market.service import DoctorRow, make_service

    _, p = _load(portfolio)

    async def go():
        svc = make_service()
        async with svc.client:
            rows = await svc.doctor(list(p.instruments.values()))
            fx = {}
            for prov in svc.fx_providers:
                try:
                    r = await prov.rate("USD", p.base_currency)
                    fx[prov.name] = f"{r.rate} ({r.as_of:%Y-%m-%d %H:%M})"
                except Exception as e:
                    fx[prov.name] = f"[red]{e}[/]"
            return rows, fx

    rows, fx = _run(go())
    providers = sorted(
        {r.provider for r in rows}, key=lambda n: [r.provider for r in rows].index(n)
    )
    t = Table(title="Quote providers")
    t.add_column("Instrument")
    for name in providers:
        t.add_column(name)
    by_ins: dict[str, dict[str, DoctorRow]] = {}
    for r in rows:
        by_ins.setdefault(r.instrument_id, {})[r.provider] = r
    for ins_id, cells in by_ins.items():
        ins = p.instruments[ins_id]
        line = [f"{ins.name[:32]}\n[dim]{ins.symbols_in_order()[0]}[/]"]
        for name in providers:
            r = cells[name]
            if r.ok:
                line.append(
                    f"[green]{r.price} {r.currency}[/]\n[dim]{r.as_of:%m-%d %H:%M} {r.ms}ms[/]"
                )
            elif r.error == "not supported":
                line.append("[dim]n/a[/]")
            else:
                line.append(f"[red]FAIL[/]\n[dim]{(r.error or '')[:40]}[/]")
        t.add_row(*line)
    console.print(t)
    ft = Table(title=f"FX USD/{p.base_currency}")
    for name in fx:
        ft.add_column(name)
    ft.add_row(*fx.values())
    console.print(ft)


@app.command()
def resolve(
    query: Annotated[str | None, typer.Argument(help="Name, ticker or ISIN")] = None,
    isin: str | None = None,
    currency: str = "EUR",
    add: bool = typer.Option(False, help="Add the (unique) match to the portfolio"),
    portfolio: PortfolioOpt = None,
):
    """Find an instrument and its listings."""
    from pluto.market.resolver import InstrumentResolver
    from pluto.market.service import make_client

    async def go():
        client = make_client()
        async with client:
            return await InstrumentResolver(client).resolve(query, isin, currency)

    res = _run(go())
    for n in res.notes:
        console.print(f"[yellow]{n}[/]")
    for m in res.matches:
        console.print(
            f"[bold]{m.name}[/]  {m.isin or '(no ISIN)'}  {m.asset_type.value}/"
            f"{m.asset_class.value}  confidence {m.confidence:.2f}"
        )
        for ls in m.listings:
            mark = "*" if ls.symbol == m.preferred_symbol else " "
            console.print(f"   {mark} {ls.symbol:<12} {ls.exchange:<6} {ls.currency}")
        for n in m.notes:
            console.print(f"     [dim]{n}[/]")
    if add:
        u = res.unique
        if u is None:
            err.print("no unique match; not adding")
            raise typer.Exit(1)
        store, p = _load(portfolio)
        ins = p.add_instrument(u.to_instrument())
        info = store.commit(p, f"Add instrument {ins.name}")
        console.print(f"Added instrument {ins.id} → version {info.version}")


# --- chat ----------------------------------------------------------------------------------


@app.command()
def chat(
    portfolio: PortfolioOpt = None,
    message: Annotated[
        str | None, typer.Option("--message", "-m", help="Send one message and exit")
    ] = None,
    provider: str | None = typer.Option(None, help="claude_agent (default) | anthropic_api"),
    model: str | None = typer.Option(None, help="Model override, e.g. sonnet, opus"),
    show_tools: bool = typer.Option(True, help="Print tool calls as they happen"),
):
    """Talk to Pluto. Transactions you describe are recorded when unambiguous."""
    from pluto.chat.prompt import system_prompt
    from pluto.chat.providers import make_provider
    from pluto.chat.session import Transcript
    from pluto.chat.tools import ChatContext, ToolRegistry

    store, p = _load(portfolio)
    ctx = ChatContext.create(store)
    registry = ToolRegistry(ctx)
    prov = make_provider(
        registry, system_prompt(p, store.head()), provider, model, cwd=paths.home()
    )
    transcript = Transcript(p.name)

    async def turn(text: str) -> None:
        transcript.user(text)
        async for ev in prov.send(text):
            transcript.event(ev)
            if ev.type == "text":
                console.print(ev.text)
            elif ev.type == "tool_call" and show_tools:
                args = ", ".join(f"{k}={v!r}" for k, v in ev.args.items())
                console.print(f"[dim]→ {ev.name}({args})[/]")
            elif ev.type == "tool_result" and show_tools:
                mark = "[dim]✓[/]" if ev.ok else "[red]✗[/]"
                console.print(f"{mark} [dim]{ev.text[:160]}[/]")
            elif ev.type == "error":
                err.print(ev.text)

    async def run() -> None:
        try:
            await prov.start()
            if message:
                await turn(message)
                return
            console.print(
                f"[bold]Pluto[/] · {p.name} · provider {prov.name}. "
                f"Type your message, 'exit' to quit."
            )
            while True:
                try:
                    text = console.input("[bold cyan]you>[/] ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if text.lower() in ("exit", "quit", "q"):
                    break
                if text:
                    await turn(text)
        finally:
            await prov.close()
            await ctx.close()

    _run(run())


# --- web -----------------------------------------------------------------------------------


@app.command()
def serve(
    portfolio: PortfolioOpt = None,
    host: str = "127.0.0.1",
    port: int = 8321,
    provider: str | None = typer.Option(None, help="claude_agent (default) | anthropic_api"),
    model: str | None = None,
    open_browser: bool = typer.Option(True, "--open/--no-open"),
):
    """Run the web UI (API + built frontend) on localhost."""
    import webbrowser

    import uvicorn

    from pluto.api.app import WEB_DIST, create_app

    _load(portfolio)
    if not WEB_DIST.exists():
        console.print(
            "[yellow]web/dist not found: API only. Build with `cd web && npm run build`.[/]"
        )
    web_app = create_app(portfolio, provider, model)
    url = f"http://{host}:{port}"
    console.print(f"Pluto at [bold]{url}[/]")
    if open_browser and WEB_DIST.exists():
        webbrowser.open(url)
    uvicorn.run(web_app, host=host, port=port, log_level="warning")
