"""Tables (section 8 of the plan)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """One NPP = one KiotViet shop."""

    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    retailer: Mapped[str] = mapped_column(String(200), unique=True)
    client_id: Mapped[str] = mapped_column(String(200))
    client_secret_enc: Mapped[str] = mapped_column(Text)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Chat(Base):
    """A Telegram chat allowed to use the bot. tenant_id NULL + role 'owner' = sees all NPPs."""

    __tablename__ = "chats"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="tenant")  # tenant | owner
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # ChatSettings, only non-defaults


class Branch(Base):
    __tablename__ = "branches"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200))


class Product(Base):
    __tablename__ = "products"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String(100), default="")
    name: Mapped[str] = mapped_column(String(500))
    name_norm: Mapped[str] = mapped_column(String(600), default="")  # unaccented name + code, for search
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    product_type: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 combo, 2 thường, 3 dịch vụ
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_batch_expire: Mapped[bool] = mapped_column(Boolean, default=False)
    # Extra selling units (thùng/hộp) point at the base-unit product.
    master_unit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    conversion_value: Mapped[float] = mapped_column(Float, default=1.0)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def is_base_unit(self) -> bool:
        return not self.master_unit_id or self.master_unit_id == self.id


class Inventory(Base):
    __tablename__ = "inventory"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    on_hand: Mapped[float] = mapped_column(Float, default=0)
    reserved: Mapped[float] = mapped_column(Float, default=0)
    min_qty: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = not set on KiotViet
    max_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[int] = mapped_column(BigInteger)
    branch_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    batch_name: Mapped[str] = mapped_column(String(200), default="")
    expire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    on_hand: Mapped[float] = mapped_column(Float, default=0)

    __table_args__ = (Index("ix_batches_tenant_product", "tenant_id", "product_id"),)


class Invoice(Base):
    __tablename__ = "invoices"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String(100), default="")
    branch_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sold_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sold_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    purchase_date: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[int] = mapped_column(Integer)  # 1 hoàn thành, 2 hủy, 3 đang xử lý, 5 không giao được
    total: Mapped[float] = mapped_column(Float, default=0)
    modified_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_invoices_tenant_date", "tenant_id", "purchase_date"),)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    invoice_id: Mapped[int] = mapped_column(BigInteger)
    product_id: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    price: Mapped[float] = mapped_column(Float, default=0)
    discount: Mapped[float] = mapped_column(Float, default=0)
    sub_total: Mapped[float | None] = mapped_column(Float, nullable=True)  # subTotal, if KiotViet sends it

    __table_args__ = (
        Index("ix_lines_tenant_invoice", "tenant_id", "invoice_id"),
        Index("ix_lines_tenant_product", "tenant_id", "product_id"),
    )


class Staff(Base):
    """KiotViet users (/users). Super Admin is not returned; invoices still carry soldByName."""

    __tablename__ = "staff"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_name: Mapped[str] = mapped_column(String(200), default="")
    given_name: Mapped[str] = mapped_column(String(200), default="")
    name_norm: Mapped[str] = mapped_column(String(500), default="")
    removed: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def display_name(self) -> str:
        return self.given_name or self.user_name or str(self.id)


class Return(Base):
    __tablename__ = "returns"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String(100), default="")
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    branch_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sold_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    received_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    return_date: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0)
    modified_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_returns_tenant_date", "tenant_id", "return_date"),)


class ReturnLine(Base):
    __tablename__ = "return_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    return_id: Mapped[int] = mapped_column(BigInteger)
    product_id: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    price: Mapped[float] = mapped_column(Float, default=0)

    __table_args__ = (Index("ix_return_lines_tenant_return", "tenant_id", "return_id"),)


class Target(Base):
    """Monthly target: kind 'revenue' (đồng) or 'product' (quantity of one product, base unit)."""

    __tablename__ = "targets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    staff_id: Mapped[int] = mapped_column(BigInteger)
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    kind: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    value: Mapped[float] = mapped_column(Float)

    __table_args__ = (Index("ix_targets_tenant_month", "tenant_id", "month"),)


class SyncState(Base):
    __tablename__ = "sync_state"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    resource: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # cursor for lastModifiedFrom
    last_success: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertState(Base):
    """5.4: one row per item that is (or recently was) over an urgent threshold.
    key = kind:product:branch[:batch]; kind = out | low | expired | red."""

    __tablename__ = "alert_state"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(400), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # still over the threshold at the last check
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    last_sent: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = not told yet
