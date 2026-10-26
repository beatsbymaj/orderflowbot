# 16. IBKR paper execution through Nautilus (Phase 7)

Checked 2026-10-26 against the installed NautilusTrader 2.0.0rc7 type stubs and the docs
below. Re-verify monthly with the other research files and whenever Nautilus is upgraded.

## Nautilus IB adapter
Source: https://nautilustrader.io/docs/latest/integrations/interactive_brokers/ (2026-10-26).
- Instrument ids: the docs' stock example is `load_ids={InstrumentId.from_str("AAPL.XNAS")}`.
  `symbol_to_mic_venue` "Override venues for matching symbol prefixes"; "Stock primary
  exchange and a compatible cached venue take precedence over SMART routing".
  `convert_exchange_to_mic_venue` "converts a known exchange to its MIC".
- "Use the same provider config for data and execution clients so both resolve identical
  IDs." FlowForge uses Alpaca data, not the IB data client, so it maps every name to XNAS
  to match the Alpaca ids.
- Order types listed: MARKET, LIMIT, MARKET_TO_LIMIT, STOP_MARKET, STOP_LIMIT,
  MARKET_IF_TOUCHED, LIMIT_IF_TOUCHED, TRAILING_STOP_MARKET, TRAILING_STOP_LIMIT. Time in
  force: DAY, GTC, IOC, FOK, GTD, AT_THE_OPEN. Post-only orders are rejected.
- Client ids: "Each process connected to the same TWS or Gateway session needs a distinct IB
  API client_id"; an execution client id "cannot be a multiple of 1000". Default 11 for the
  node (and the watchdog after the node is gone, ADR-0013), 91 for the read-only check.
- Reconciliation: mass status from open and completed orders, executions with commission
  reports, and positions; "the execution engine can adopt the position from the position
  report alone"; after a restart "orders a previous session left working return as open
  external orders".
- Stubs (installed package, 2026-10-26): `LiveNodeBuilder.add_exec_client(name, factory,
  config, routing=RoutingConfig(default=..., venues=...))`; `InteractiveBrokersExecutionClientConfig`
  takes `instrument_provider`; `InteractiveBrokersInstrumentProviderConfig` takes `load_ids`,
  `convert_exchange_to_mic_venue`, `symbol_to_mic_venue`.

## UNVERIFIED (the drills check these on the paper gateway)
- That `SPY.XNAS` loads SPY (an NYSE Arca listing) and that orders on it route sensibly
  (SMART vs. direct to Nasdaq). The page doesn't say how the contract exchange is chosen for
  orders beyond a per-command `params` override. Drill `instruments` checks the ids and tick
  size; the round-trip report shows the fills.
- That routing by `RoutingConfig(default=True)` sends XNAS orders to the IB client.
- `reduce_only` handling: not mentioned in the docs. FlowForge never relies on it; every
  sell is capped at the position by RiskGuard (`check_exit`).
- Outside-RTH flags for orders: not documented; FlowForge trades regular hours only.
- IOC behaviour of IBKR paper fills (simulated against the live quote). Measured by drill.

## IBKR paper account
Source: https://www.interactivebrokers.com/campus/trading-lessons/using-ibkrs-paper-trading-account/
(2026-10-26).
- "reset your simulated funds back to $1 million, or another amount, at any time" via Client
  Portal Account Settings.
- The paper account "will share the same market data permissions as your live account";
  without a real-time subscription paper quotes are delayed.
- **UNVERIFIED:** whether the paper account enforces the old pattern-day-trader rule or the
  new intraday margin rules (docs/research/05: FINRA replaced PDT on 2026-06-04; brokers may
  move accounts over until 2027-10-20). risk_limits.yaml keeps `legacy_pdt` until the
  operator confirms the account's regime.

## Cash-account mode (`pdt_regime: cash`, added 2026-10-26 at the operator's request)
Rules (docs/research/05, VERIFIED at the regulatory level): a cash account has no day-trade
count; sales settle T+1; buying with unsettled proceeds and selling before they settle is a
good-faith violation; freeriding can bring a 90-day freeze.
- What FlowForge does: every sale fill is recorded in the risk state as "settles next trading
  day". RiskGuard sizes each buy by `by_settled_cash` = broker cash - pending buys - sales not
  yet settled. In practice the bot can buy about the account's settled cash once a day
  (with $10,000: about $10,000 of buys a day, any number of trades inside that).
- The walk-forward account replay and the backtest LimitBook apply the same rule.
- **UNVERIFIED:** whether IBKR's reported free cash already leaves out unsettled proceeds
  (IBKR's cash-account pages redirect and couldn't be read on 2026-10-26). If it does, the
  bot subtracts them twice, which only makes it buy less (safe side).
- **UNVERIFIED:** whether the IBKR paper account behaves like a cash account when the live
  account is one. Check in Client Portal (Settings, Account type) before relying on it.
- Sales made by the independent watchdog aren't recorded (it doesn't use the risk state);
  they happen only in emergencies, after which trading is halted anyway.
