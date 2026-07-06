"""Modelos ORM (SQLAlchemy 2.0). Reflejan el esquema de la sección 16 de
ESPECIFICACION_SISTEMA_TRADING.md. Solo se declaran aquí las tablas que
corresponden a fases ya implementadas (fase 0: assets, candles,
market_snapshots; fase 1: regime_log, decision_logs, trade_entries,
trade_exits, position_events, equity_snapshots, system_state)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, PrimaryKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


NUMERIC = Numeric(precision=28, scale=10)
TIMESTAMPTZ = DateTime(timezone=True)


class Asset(Base):
    __tablename__ = "assets"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    base: Mapped[str] = mapped_column(String(10))
    quote: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (PrimaryKeyConstraint("asset", "timeframe", "open_time"),)

    asset: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(2))
    open_time: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    open: Mapped[Decimal] = mapped_column(NUMERIC)
    high: Mapped[Decimal] = mapped_column(NUMERIC)
    low: Mapped[Decimal] = mapped_column(NUMERIC)
    close: Mapped[Decimal] = mapped_column(NUMERIC)
    volume: Mapped[Decimal] = mapped_column(NUMERIC)
    quote_volume: Mapped[Decimal] = mapped_column(NUMERIC)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String(20))
    ts: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    bid: Mapped[Decimal] = mapped_column(NUMERIC)
    ask: Mapped[Decimal] = mapped_column(NUMERIC)
    spread_bps: Mapped[Decimal] = mapped_column(NUMERIC)
    quote_vol_24h: Mapped[Decimal] = mapped_column(NUMERIC)
    change_24h_pct: Mapped[Decimal] = mapped_column(NUMERIC)
    raw_jsonb: Mapped[dict] = mapped_column(JSONB)


class RegimeLog(Base):
    __tablename__ = "regime_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    btc_regime: Mapped[str] = mapped_column(String(20))
    atr_pct: Mapped[Decimal] = mapped_column(NUMERIC)
    details_jsonb: Mapped[dict] = mapped_column(JSONB)


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    mode: Mapped[str] = mapped_column(String(30))
    trigger: Mapped[str] = mapped_column(String(10))
    asset: Mapped[str] = mapped_column(String(20))
    git_sha: Mapped[str] = mapped_column(String(40))
    scanner_jsonb: Mapped[dict] = mapped_column(JSONB)
    technical_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fundamental_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decision_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_verdict_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    final_action: Mapped[str] = mapped_column(String(20))
    rejection_reasons: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    expected_tp: Mapped[Decimal | None] = mapped_column(NUMERIC, nullable=True)
    expected_sl: Mapped[Decimal | None] = mapped_column(NUMERIC, nullable=True)
    horizon_class: Mapped[str | None] = mapped_column(String(10), nullable=True)


class TradeEntry(Base):
    __tablename__ = "trade_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    decision_log_id: Mapped[int] = mapped_column(ForeignKey("decision_logs.id"))
    asset: Mapped[str] = mapped_column(String(20))
    environment: Mapped[str] = mapped_column(String(10))
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_time: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    entry_price: Mapped[Decimal] = mapped_column(NUMERIC)
    qty: Mapped[Decimal] = mapped_column(NUMERIC)
    tp: Mapped[Decimal] = mapped_column(NUMERIC)
    sl: Mapped[Decimal] = mapped_column(NUMERIC)
    oco_list_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str | None] = mapped_column(String(2), nullable=True)
    horizon_class: Mapped[str | None] = mapped_column(String(10), nullable=True)
    invalidation_level: Mapped[Decimal | None] = mapped_column(NUMERIC, nullable=True)


class TradeExit(Base):
    __tablename__ = "trade_exits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_entry_id: Mapped[int] = mapped_column(ForeignKey("trade_entries.id"))
    exit_time: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    exit_price: Mapped[Decimal] = mapped_column(NUMERIC)
    exit_qty: Mapped[Decimal] = mapped_column(NUMERIC)
    exit_type: Mapped[str] = mapped_column(String(20))
    fees_paid: Mapped[Decimal] = mapped_column(NUMERIC)
    pnl_quote: Mapped[Decimal] = mapped_column(NUMERIC)
    pnl_pct_net: Mapped[Decimal] = mapped_column(NUMERIC)


class PositionEvent(Base):
    __tablename__ = "position_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_entry_id: Mapped[int] = mapped_column(ForeignKey("trade_entries.id"))
    ts: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    event_type: Mapped[str] = mapped_column(String(30))
    payload_jsonb: Mapped[dict] = mapped_column(JSONB)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    environment: Mapped[str] = mapped_column(String(10))
    equity_quote: Mapped[Decimal] = mapped_column(NUMERIC)
    open_positions: Mapped[int] = mapped_column(Integer)
    drawdown_pct: Mapped[Decimal] = mapped_column(NUMERIC)


class NewsItem(Base):
    """Almacén point-in-time inmutable (sección 12.1, fase 2). Append-only:
    nunca se hace UPDATE sobre una fila; una reclasificación futura será
    una fila nueva en `item_classifications` (todavía no existe — llega
    con el clasificador)."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    raw_jsonb: Mapped[dict] = mapped_column(JSONB)


class SocialItem(Base):
    """Almacén point-in-time inmutable (sección 12.1, fase 2) — Reddit.
    Append-only, igual que `NewsItem`: nunca UPDATE; idempotente por
    `post_id` (identificador propio de Reddit, no hace falta un
    `content_hash` calculado)."""

    __tablename__ = "social_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20))
    subreddit: Mapped[str] = mapped_column(String(50))
    post_id: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_at_fetch: Mapped[int] = mapped_column(Integer)
    num_comments_at_fetch: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    raw_jsonb: Mapped[dict] = mapped_column(JSONB)


class ItemClassification(Base):
    """Clasificación del clasificador Ollama (sección 12.3) sobre un
    `NewsItem`/`SocialItem` (`item_kind`+`item_id`, sin FK porque es
    polimórfico). Append-only, igual que el resto del almacén PIT:
    reclasificar (otro modelo, otra versión) es una fila nueva, nunca un
    UPDATE.

    `asset_tags` no está en el schema sketch de la sección 12.1 — ver
    `# DECISION` en la migración `0006_item_classifications.py`."""

    __tablename__ = "item_classifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(Integer)
    item_kind: Mapped[str] = mapped_column(String(10))
    model_name: Mapped[str] = mapped_column(String(60))
    model_version: Mapped[str] = mapped_column(String(20))
    classified_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    stance: Mapped[str] = mapped_column(String(20))
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    veto: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str] = mapped_column(Text)
    output_jsonb: Mapped[dict] = mapped_column(JSONB)
    asset_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


class ClassifierScorecard(Base):
    """Evaluación semanal del clasificador (sección 12.3/16): hit-rate y
    retorno medio de cada `stance` contra retornos realizados a
    4h/24h/72h. Decide si la capa fundamental aporta señal real."""

    __tablename__ = "classifier_scorecard"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    week: Mapped[date] = mapped_column(Date)
    stance: Mapped[str] = mapped_column(String(20))
    horizon: Mapped[str] = mapped_column(String(5))
    n: Mapped[int] = mapped_column(Integer)
    hit_rate: Mapped[Decimal] = mapped_column(Numeric(precision=6, scale=4))
    avg_fwd_return: Mapped[Decimal] = mapped_column(Numeric(precision=10, scale=6))


class SystemState(Base):
    """Fila única (id=1) con el estado global running/halt.

    # DECISION: tabla no listada explícitamente en la sección 16, pero
    # necesaria para persistir el killswitch (sección 9.2: "requiere
    # rearme manual por CLI/endpoint") a través de reinicios del proceso.
    """

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String(10), default="running")
    halted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    halted_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rearmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
