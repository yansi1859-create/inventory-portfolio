# imports - standard imports
import json
import os
import re
import secrets
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

# imports - third party imports
from flask import Flask, has_request_context, redirect, render_template, request, session

DATABASE_NAME = "inventory.sqlite"
_DATABASE_PATH = Path(__file__).parent.parent / DATABASE_NAME
VIEWS = {
    "Summary": "/",
    "Stock": "/product",
    "Warehouses": "/location",
    "Logistics": "/movement",
}
EMPTY_SYMBOLS = {"", " ", None}

app = Flask(__name__)
app.secret_key = os.environ.get("INVENTORY_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


class BusinessError(ValueError):
    """An invalid operation that can be explained to the user."""


def positive_integer(value):
    value = str(value or "")
    if not re.fullmatch(r"[0-9]{1,10}", value) or not 0 < int(value) <= 2147483647:
        raise BusinessError("数量或编号必须为正整数，且不能超过 2147483647。")
    return int(value)


@contextmanager
def database():
    """Own the transaction AND close the connection, including on errors."""
    conn = sqlite3.connect(DATABASE_NAME, timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # Serialize the check-and-write sequence for all data-changing requests.
        if has_request_context() and request.method == "POST":
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def protect_changes():
    if request.method == "POST":
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            return (
                render_template(
                    "error.jinja", link=VIEWS, title="操作失败", message="表单凭据无效，请返回页面刷新后重试。"
                ),
                400,
            )


@app.errorhandler(BusinessError)
def invalid_operation(error):
    return render_template("error.jinja", link=VIEWS, title="操作失败", message=str(error)), 400


@app.errorhandler(sqlite3.IntegrityError)
def duplicate_or_reference(error):
    message = "名称已存在，请使用其他名称。" if "UNIQUE" in str(error) else "资料引用无效，操作未保存。"
    return render_template("error.jinja", link=VIEWS, title="操作失败", message=message), 409


if os.environ.get("FLASK_DEBUG") == "1":
    app.config.update(TEMPLATES_AUTO_RELOAD=True)
    DATABASE_NAME = _DATABASE_PATH.resolve()
else:
    DATABASE_NAME = os.environ.get("DATABASE_NAME") or _DATABASE_PATH.resolve()


def init_database():
    PRODUCTS = (
        "products("
        "prod_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "prod_name TEXT UNIQUE NOT NULL, "
        "prod_quantity INTEGER NOT NULL, "
        "unallocated_quantity INTEGER)"
    )
    LOCATIONS = "location(loc_id INTEGER PRIMARY KEY AUTOINCREMENT, loc_name TEXT UNIQUE NOT NULL)"
    LOGISTICS = (
        "logistics("
        "trans_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "prod_id INTEGER NOT NULL, "
        "from_loc_id INTEGER NULL, "
        "to_loc_id INTEGER NULL, "
        "prod_quantity INTEGER NOT NULL, "
        "trans_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(prod_id) REFERENCES products(prod_id), "
        "FOREIGN KEY(from_loc_id) REFERENCES location(loc_id), "
        "FOREIGN KEY(to_loc_id) REFERENCES location(loc_id))"
    )

    with database() as conn:
        for table_definition in [PRODUCTS, LOCATIONS, LOGISTICS]:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_definition}")
        for table in ("products", "location"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "active" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS default_prod_qty_to_unalloc_qty "
            "AFTER INSERT ON products FOR EACH ROW WHEN NEW.unallocated_quantity IS NULL "
            "BEGIN UPDATE products SET unallocated_quantity = NEW.prod_quantity WHERE rowid = NEW.rowid; END"
        )


app.init_db = init_database


@app.route("/", methods=["GET"])
def summary():
    with database() as conn:
        warehouse = conn.execute("SELECT * FROM location").fetchall()
        products = conn.execute("SELECT * FROM products").fetchall()
        q_data = conn.execute(
            "SELECT prod_name, unallocated_quantity, prod_quantity FROM products"
        ).fetchall()

    return render_template(
        "index.jinja",
        link=VIEWS,
        title="Summary",
        warehouses=warehouse,
        products=products,
        summary=q_data,
    )


@app.route("/product", methods=["POST", "GET"])
def product():
    with database() as conn:
        if request.method == "POST":
            prod_name = request.form.get("prod_name", "").strip()
            quantity = positive_integer(request.form.get("prod_quantity"))
            if not prod_name:
                raise BusinessError("商品名称不能为空。")
            conn.execute(
                "INSERT INTO products (prod_name, prod_quantity) VALUES (?, ?)",
                (prod_name, quantity),
            )
            return redirect(VIEWS["Stock"])

        products = conn.execute("SELECT * FROM products").fetchall()

    return render_template(
        "product.jinja",
        link=VIEWS,
        products=products,
        title="Stock",
    )


@app.route("/location", methods=["POST", "GET"])
def location():
    with database() as conn:
        if request.method == "POST":
            warehouse_name = request.form.get("warehouse_name", "").strip()
            if not warehouse_name:
                raise BusinessError("仓库名称不能为空。")
            conn.execute("INSERT INTO location (loc_name) VALUES (?)", (warehouse_name,))
            return redirect(VIEWS["Warehouses"])

        warehouse_data = conn.execute("SELECT * FROM location").fetchall()

    return render_template(
        "location.jinja",
        link=VIEWS,
        warehouses=warehouse_data,
        title="Warehouses",
    )


def get_warehouse_data(
    conn: sqlite3.Connection, products: list[tuple], locations: list[tuple]
) -> list[tuple]:
    log_summary = []
    for p_id in [x[0] for x in products]:
        temp_prod_name = conn.execute(
            "SELECT prod_name FROM products WHERE prod_id = ?", (p_id,)
        ).fetchone()

        for l_id in [x[0] for x in locations]:
            temp_loc_name = conn.execute(
                "SELECT loc_name FROM location WHERE loc_id = ?", (l_id,)
            ).fetchone()
            sum_to_loc = conn.execute(
                "SELECT SUM(log.prod_quantity) FROM logistics log WHERE log.prod_id = ? AND log.to_loc_id = ?",
                (p_id, l_id),
            ).fetchone()
            sum_from_loc = conn.execute(
                "SELECT SUM(log.prod_quantity) FROM logistics log WHERE log.prod_id = ? AND log.from_loc_id = ?",
                (p_id, l_id),
            ).fetchone()
            log_summary += [
                temp_prod_name + temp_loc_name + ((sum_to_loc[0] or 0) - (sum_from_loc[0] or 0),)
            ]

    return log_summary


def update_warehouse_data(conn: sqlite3.Connection):
    quantity = positive_integer(request.form.get("quantity"))
    prod_name = request.form.get("prod_name", "").strip()
    from_loc = request.form.get("from_loc", "").strip()
    to_loc = request.form.get("to_loc", "").strip()
    product = conn.execute(
        "SELECT prod_id, unallocated_quantity FROM products WHERE prod_name = ? AND active = 1",
        (prod_name,),
    ).fetchone()
    if not product:
        raise BusinessError("商品不存在或已停用。")
    if not from_loc and not to_loc:
        raise BusinessError("至少选择一个仓库。")
    if from_loc == to_loc:
        raise BusinessError("来源仓库和目标仓库不能相同。")
    locations = []
    for name in (from_loc, to_loc):
        row = (
            conn.execute(
                "SELECT loc_id FROM location WHERE loc_name = ? AND active = 1", (name,)
            ).fetchone()
            if name
            else None
        )
        if name and not row:
            raise BusinessError("仓库不存在或已停用。")
        locations.append(row[0] if row else None)
    from_id, to_id = locations
    prod_id, unallocated = product
    available = unallocated
    if from_id is not None:
        available = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN to_loc_id = ? THEN prod_quantity ELSE 0 END), 0) "
            "- COALESCE(SUM(CASE WHEN from_loc_id = ? THEN prod_quantity ELSE 0 END), 0) "
            "FROM logistics WHERE prod_id = ?",
            (from_id, from_id, prod_id),
        ).fetchone()[0]
    if quantity > available:
        raise BusinessError(f"库存不足，可用数量为 {available}。")
    conn.execute(
        "INSERT INTO logistics (prod_id, from_loc_id, to_loc_id, prod_quantity) VALUES (?, ?, ?, ?)",
        (prod_id, from_id, to_id, quantity),
    )
    if from_id is None or to_id is None:
        change = -quantity if from_id is None else quantity
        conn.execute(
            "UPDATE products SET unallocated_quantity = unallocated_quantity + ? WHERE prod_id = ?",
            (change, prod_id),
        )


def get_warehouse_map(log_summary: list):
    # summary data --> in format:
    # {'Asus Zenfone 2': {'Mahalakshmi': 50, 'Gorhe': 50},
    # 'Prada watch': {'Malad': 50, 'Mahalakshmi': 115}, 'Apple iPhone': {'Airoli': 75}}
    item_location_qty_map = defaultdict(dict)
    for row in log_summary:
        if row[1] in item_location_qty_map[row[0]]:
            item_location_qty_map[row[0]][row[1]] += row[2]
        else:
            item_location_qty_map[row[0]][row[1]] = row[2]
    return json.dumps(item_location_qty_map)


@app.route("/movement", methods=["POST", "GET"])
def movement():
    match request.method:
        case "GET":
            with database() as conn:
                logistics_data = conn.execute("SELECT * FROM logistics").fetchall()
                products = conn.execute(
                    "SELECT prod_id, prod_name, unallocated_quantity FROM products WHERE active = 1"
                ).fetchall()
                locations = conn.execute(
                    "SELECT loc_id, loc_name FROM location WHERE active = 1"
                ).fetchall()
                warehouse_summary = get_warehouse_data(conn, products, locations)
                item_location_qty_map = get_warehouse_map(warehouse_summary)
                return render_template(
                    "movement.jinja",
                    title="Logistics",
                    link=VIEWS,
                    products=products,
                    locations=locations,
                    allocated=item_location_qty_map,
                    logistics=logistics_data,
                    summary=warehouse_summary,
                )

        case "POST":
            with database() as conn:
                update_warehouse_data(conn)
                return redirect(VIEWS["Logistics"])


@app.route("/delete", methods=["POST"])
def delete():
    record_type = request.args.get("type")
    if record_type not in {"product", "location"}:
        raise BusinessError("资料类型无效。")
    table, key, destination = (
        ("products", "prod_id", VIEWS["Stock"])
        if record_type == "product"
        else ("location", "loc_id", VIEWS["Warehouses"])
    )
    record_id = positive_integer(request.form.get(key))
    with database() as conn:
        if not conn.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (record_id,)).fetchone():
            raise BusinessError("资料不存在。")
        history = conn.execute(
            "SELECT 1 FROM logistics WHERE prod_id = ? LIMIT 1"
            if record_type == "product"
            else "SELECT 1 FROM logistics WHERE from_loc_id = ? OR to_loc_id = ? LIMIT 1",
            (record_id,) if record_type == "product" else (record_id, record_id),
        ).fetchone()
        if history:
            conn.execute(f"UPDATE {table} SET active = 0 WHERE {key} = ?", (record_id,))
        else:
            conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (record_id,))
    return redirect(destination)


@app.route("/edit", methods=["POST"])
def edit():
    record_type = request.args.get("type")
    if record_type not in {"product", "location"}:
        raise BusinessError("资料类型无效。")
    table, key, name_key, destination = (
        ("products", "prod_id", "prod_name", VIEWS["Stock"])
        if record_type == "product"
        else ("location", "loc_id", "loc_name", VIEWS["Warehouses"])
    )
    record_id = positive_integer(request.form.get(key))
    name = request.form.get(name_key, "").strip()
    with database() as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {key} = ? AND active = 1", (record_id,)
        ).fetchone()
        if not row:
            raise BusinessError("资料不存在或已停用。")
        if name:
            conn.execute(f"UPDATE {table} SET {name_key} = ? WHERE {key} = ?", (name, record_id))
        raw_quantity = request.form.get("prod_quantity", "")
        if record_type == "product" and raw_quantity != "":
            quantity = positive_integer(raw_quantity)
            if quantity < row[2]:
                raise BusinessError("原库存分配模式仅允许增加总量，不能直接减少库存。")
            conn.execute(
                "UPDATE products SET prod_quantity = ?, unallocated_quantity = unallocated_quantity + ? WHERE prod_id = ?",
                (quantity, quantity - row[2], record_id),
            )
    return redirect(destination)


with app.app_context():
    app.init_db()
