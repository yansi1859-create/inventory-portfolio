"""Single-warehouse portfolio edition based on Gavin D'souza's MIT project.

The repaired original allocation application is preserved in inventory.legacy.
This entry point exclusively derives stock from posted ledger entries.
"""
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Flask,
    flash,
    has_request_context,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("INVENTORY_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=65536
)
DATABASE_NAME = os.environ.get("DATABASE_NAME") or str(
    Path(__file__).parent.parent / "portfolio.sqlite"
)
KINDS = {"opening": "期初入库", "purchase": "采购入库", "sale": "销售出库"}
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
MAX_INTEGER = 2147483647


class BusinessError(ValueError):
    pass


def integer(value, minimum=1):
    raw = str(value) if value is not None else ""
    if not re.fullmatch(r"[0-9]{1,10}", raw) or not minimum <= int(raw) <= MAX_INTEGER:
        description = "正整数" if minimum else "非负整数"
        raise BusinessError(f"数量或编号必须为{description}，且不能超过 {MAX_INTEGER}。")
    return int(raw)


def text(value, label, limit):
    value = (value or "").strip()
    if not value or len(value) > limit:
        raise BusinessError(f"{label}不能为空，且不能超过 {limit} 个字符。")
    return value


@contextmanager
def database():
    conn = sqlite3.connect(DATABASE_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if has_request_context() and request.method == "POST":
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    with database() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL UNIQUE,
                unit TEXT NOT NULL,
                safety_stock INTEGER NOT NULL DEFAULT 0 CHECK (typeof(safety_stock) = 'integer' AND safety_stock >= 0),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('opening', 'purchase', 'sale')),
                product_id INTEGER NOT NULL REFERENCES catalog(id),
                quantity INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity > 0),
                business_date TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'posted')),
                product_code TEXT NOT NULL,
                product_name TEXT NOT NULL,
                product_unit TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                posted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS stock_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id),
                product_id INTEGER NOT NULL REFERENCES catalog(id),
                change_quantity INTEGER NOT NULL CHECK (typeof(change_quantity) = 'integer' AND change_quantity != 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ledger_product ON stock_ledger(product_id);
            CREATE UNIQUE INDEX IF NOT EXISTS one_posted_opening
                ON documents(product_id) WHERE kind = 'opening' AND state = 'posted';
            CREATE TRIGGER IF NOT EXISTS posted_document_no_update
                BEFORE UPDATE ON documents WHEN OLD.state = 'posted'
                BEGIN SELECT RAISE(ABORT, 'posted document is read only'); END;
            CREATE TRIGGER IF NOT EXISTS posted_document_no_delete
                BEFORE DELETE ON documents WHEN OLD.state = 'posted'
                BEGIN SELECT RAISE(ABORT, 'posted document is read only'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_no_update
                BEFORE UPDATE ON stock_ledger
                BEGIN SELECT RAISE(ABORT, 'ledger is read only'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_no_delete
                BEFORE DELETE ON stock_ledger
                BEGIN SELECT RAISE(ABORT, 'ledger is read only'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_requires_posted_document
                BEFORE INSERT ON stock_ledger
                WHEN NOT EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = NEW.document_id
                    AND d.state = 'posted' AND d.product_id = NEW.product_id
                    AND NEW.change_quantity = CASE WHEN d.kind = 'sale' THEN -d.quantity ELSE d.quantity END
                )
                BEGIN SELECT RAISE(ABORT, 'ledger must match posted document'); END;
        """
        )


app.init_db = init_database


def csrf_token():
    if "form_token" not in session:
        session["form_token"] = secrets.token_hex(32)
    return session["form_token"]


@app.context_processor
def template_helpers():
    return {
        "csrf_token": csrf_token,
        "kinds": KINDS,
        "today": datetime.now(LOCAL_TIMEZONE).date().isoformat(),
    }


@app.template_filter("local_time")
def local_time(value):
    if not value:
        return "—"
    return (
        datetime.fromisoformat(value)
        .replace(tzinfo=timezone.utc)
        .astimezone(LOCAL_TIMEZONE)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


@app.before_request
def protect_changes():
    if request.method == "POST":
        expected = session.get("form_token", "")
        actual = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected.encode(), actual.encode()):
            raise BusinessError("表单凭据无效，请返回页面刷新后重试。")


@app.errorhandler(BusinessError)
def business_error(error):
    return render_template("mvp/error.jinja", message=str(error)), 400


@app.errorhandler(sqlite3.IntegrityError)
def integrity_error(error):
    if "catalog.code" in str(error):
        message = "商品编码已存在，请使用其他编码。"
    elif "catalog.name" in str(error):
        message = "商品名称已存在，请使用其他名称。"
    else:
        message = "操作与已有记录冲突，本次操作未保存。请刷新后检查单据状态。"
    return render_template("mvp/error.jinja", message=message), 409


@app.errorhandler(sqlite3.OperationalError)
def database_error(error):
    if "locked" not in str(error):
        raise error
    return render_template("mvp/error.jinja", message="数据库正在处理其他操作，请稍后刷新并检查单据状态。"), 409


def product_record(conn, product_id, require_active=False):
    row = conn.execute("SELECT * FROM catalog WHERE id = ?", (product_id,)).fetchone()
    if not row or (require_active and not row["active"]):
        raise BusinessError("商品不存在或已停用。")
    return row


def document_record(conn, document_id):
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if not row:
        raise BusinessError("单据不存在。")
    return row


def stock(conn, product_id):
    return conn.execute(
        "SELECT COALESCE(SUM(l.change_quantity), 0) FROM stock_ledger l "
        "JOIN documents d ON d.id = l.document_id WHERE l.product_id = ? AND d.state = 'posted'",
        (product_id,),
    ).fetchone()[0]


def product_values():
    if "balance" in request.form or "prod_quantity" in request.form:
        raise BusinessError("禁止直接修改库存余额，请通过单据确认产生库存变化。")
    return (
        text(request.form.get("code"), "商品编码", 40),
        text(request.form.get("name"), "商品名称", 100),
        text(request.form.get("unit"), "单位", 20),
        integer(request.form.get("safety_stock", "0"), minimum=0),
    )


def document_values(conn):
    kind = request.form.get("kind")
    if kind not in KINDS:
        raise BusinessError("单据类型无效。")
    product = product_record(conn, integer(request.form.get("product_id")), require_active=True)
    quantity = integer(request.form.get("quantity"))
    raw_date = request.form.get("business_date", "")
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
            raise ValueError
        date.fromisoformat(raw_date)
    except ValueError:
        raise BusinessError("业务日期必须是有效的年-月-日，例如 2026-10-13。") from None
    note = request.form.get("note", "").strip()
    if len(note) > 500:
        raise BusinessError("备注不能超过 500 个字符。")
    return (
        kind,
        product["id"],
        quantity,
        raw_date,
        note,
        product["code"],
        product["name"],
        product["unit"],
    )


@app.route("/")
@app.route("/stock")
def inventory():
    with database() as conn:
        products = conn.execute(
            """
            SELECT p.*, COALESCE(s.balance, 0) AS balance FROM catalog p
            LEFT JOIN (
                SELECT l.product_id, SUM(l.change_quantity) AS balance FROM stock_ledger l
                JOIN documents d ON d.id = l.document_id WHERE d.state = 'posted' GROUP BY l.product_id
            ) s ON s.product_id = p.id ORDER BY p.id
        """
        ).fetchall()
    low_only = request.args.get("low") == "1"
    if low_only:
        products = [p for p in products if p["active"] and p["balance"] <= p["safety_stock"]]
    return render_template("mvp/stock.jinja", products=products, low_only=low_only)


@app.route("/products", methods=["GET", "POST"])
def products():
    if request.method == "POST":
        values = product_values()
        with database() as conn:
            conn.execute(
                "INSERT INTO catalog (code, name, unit, safety_stock) VALUES (?, ?, ?, ?)", values
            )
        flash("商品已新增，当前库存为 0。请创建期初或采购入库单。")
        return redirect(url_for("products"))
    with database() as conn:
        data = conn.execute("SELECT * FROM catalog ORDER BY id").fetchall()
    return render_template("mvp/products.jinja", products=data)


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def edit_product(product_id):
    with database() as conn:
        product = product_record(conn, product_id, require_active=True)
        if request.method == "POST":
            values = product_values()
            has_history = conn.execute(
                "SELECT 1 FROM documents WHERE product_id = ? LIMIT 1", (product_id,)
            ).fetchone()
            if has_history and (values[0] != product["code"] or values[2] != product["unit"]):
                raise BusinessError("已有单据的商品不能修改编码或单位，可修改名称和安全库存。")
            conn.execute(
                "UPDATE catalog SET code=?, name=?, unit=?, safety_stock=? WHERE id=?",
                (*values, product_id),
            )
            flash("商品资料已保存，库存未改变。")
            return redirect(url_for("products"))
    return render_template("mvp/product_edit.jinja", product=product)


@app.post("/products/<int:product_id>/delete")
def delete_product(product_id):
    with database() as conn:
        product_record(conn, product_id)
        has_history = conn.execute(
            "SELECT 1 FROM documents WHERE product_id = ? LIMIT 1", (product_id,)
        ).fetchone()
        if has_history:
            conn.execute("UPDATE catalog SET active=0 WHERE id=?", (product_id,))
            flash("商品已停用，历史单据和库存保留。")
        else:
            conn.execute("DELETE FROM catalog WHERE id=?", (product_id,))
            flash("无业务记录的商品已删除。")
    return redirect(url_for("products"))


@app.route("/documents", methods=["GET", "POST"])
def documents():
    with database() as conn:
        if request.method == "POST":
            values = document_values(conn)
            cursor = conn.execute(
                "INSERT INTO documents (kind, product_id, quantity, business_date, note, product_code, product_name, product_unit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            flash("草稿已保存，库存尚未改变。")
            return redirect(url_for("document", document_id=cursor.lastrowid))
        data = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
        products = conn.execute("SELECT * FROM catalog WHERE active=1 ORDER BY id").fetchall()
    return render_template("mvp/documents.jinja", documents=data, products=products)


@app.route("/documents/<int:document_id>", methods=["GET", "POST"])
def document(document_id):
    with database() as conn:
        record = document_record(conn, document_id)
        if request.method == "POST":
            if record["state"] != "draft":
                raise BusinessError("已过账单据只读，不能修改。")
            values = document_values(conn)
            conn.execute(
                "UPDATE documents SET kind=?, product_id=?, quantity=?, business_date=?, note=?, "
                "product_code=?, product_name=?, product_unit=? WHERE id=?",
                (*values, document_id),
            )
            flash("草稿已更新，库存尚未改变。")
            return redirect(url_for("document", document_id=document_id))
        products = conn.execute("SELECT * FROM catalog WHERE active=1 ORDER BY id").fetchall()
    return render_template("mvp/document.jinja", document=record, products=products)


@app.post("/documents/<int:document_id>/confirm")
def confirm_document(document_id):
    with database() as conn:
        record = document_record(conn, document_id)
        if record["state"] == "posted":
            flash("此单据已过账，本次未重复生成流水。")
            return redirect(url_for("document", document_id=document_id))
        product = product_record(conn, record["product_id"], require_active=True)
        quantity = integer(record["quantity"])
        balance = stock(conn, product["id"])
        if (
            record["kind"] == "opening"
            and conn.execute(
                "SELECT 1 FROM stock_ledger WHERE product_id=? LIMIT 1", (product["id"],)
            ).fetchone()
        ):
            raise BusinessError("期初入库必须是该商品的第一笔已过账业务，且只能确认一次。")
        if record["kind"] == "sale" and quantity > balance:
            raise BusinessError(f"库存不足：当前 {balance}，本次出库 {quantity}。草稿保留，库存未改变。")
        conn.execute(
            "UPDATE documents SET state='posted', posted_at=CURRENT_TIMESTAMP, "
            "product_code=?, product_name=?, product_unit=? WHERE id=?",
            (product["code"], product["name"], product["unit"], document_id),
        )
        conn.execute(
            "INSERT INTO stock_ledger (document_id, product_id, change_quantity) VALUES (?, ?, ?)",
            (document_id, product["id"], -quantity if record["kind"] == "sale" else quantity),
        )
    flash("确认成功，已过账并生成一条库存流水。")
    return redirect(url_for("document", document_id=document_id))


@app.route("/ledger")
def ledger():
    with database() as conn:
        data = conn.execute(
            """
            SELECT l.*, d.kind, d.business_date, d.product_code, d.product_name, d.product_unit
            FROM stock_ledger l JOIN documents d ON d.id=l.document_id
            WHERE d.state='posted' ORDER BY l.id
        """
        ).fetchall()
    running = {}
    entries = []
    for row in data:
        item = dict(row)
        running[row["product_id"]] = running.get(row["product_id"], 0) + row["change_quantity"]
        item["balance_after"] = running[row["product_id"]]
        entries.append(item)
    return render_template("mvp/ledger.jinja", entries=entries)


with app.app_context():
    app.init_db()
