"""Actual database acceptance tests for the single-item document workflow."""
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from bs4 import BeautifulSoup

import inventory.app as module


@pytest.fixture
def client(monkeypatch, tmp_path):
    path = tmp_path / "business.sqlite"
    monkeypatch.setattr(module, "DATABASE_NAME", str(path))
    monkeypatch.setitem(module.app.config, "TESTING", True)
    module.app.init_db()
    client = module.app.test_client()
    with client.session_transaction() as session:
        session["form_token"] = "business-tests"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "business-tests"
    yield client
    path.unlink()


def rows(sql, parameters=()):
    with module.database() as conn:
        return [tuple(row) for row in conn.execute(sql, parameters).fetchall()]


def balance(product_id=1):
    with module.database() as conn:
        return module.stock(conn, product_id)


def product(client, **changes):
    values = {"code": "BOOK-001", "name": "方格笔记本", "unit": "本", "safety_stock": "15"}
    values.update(changes)
    return client.post("/products", data=values)


def draft(client, kind="opening", quantity="20", **changes):
    values = {
        "kind": kind,
        "quantity": quantity,
        "product_id": "1",
        "business_date": "2026-10-13",
        "note": "测试",
    }
    values.update(changes)
    return client.post("/documents", data=values)


def document_id(response):
    assert response.status_code == 302
    return int(response.headers["Location"].rsplit("/", 1)[-1])


def confirm(client, record_id):
    return client.post(f"/documents/{record_id}/confirm")


def initial_stock(client, quantity="20"):
    assert product(client).status_code == 302
    record_id = document_id(draft(client, quantity=quantity))
    assert confirm(client, record_id).status_code == 302
    return record_id


def test_acceptance_opening_purchase_sale_and_readonly_ledger(client):
    assert product(client).status_code == 302
    for kind, quantity, expected in [
        ("opening", "20", 20),
        ("purchase", "10", 30),
        ("sale", "15", 15),
    ]:
        previous = balance()
        record_id = document_id(draft(client, kind, quantity))
        assert balance() == previous
        assert rows("SELECT state FROM documents WHERE id=?", (record_id,)) == [("draft",)]
        assert confirm(client, record_id).status_code == 302
        assert balance() == expected
    assert rows("SELECT change_quantity FROM stock_ledger ORDER BY id") == [(20,), (10,), (-15,)]
    assert rows("SELECT state FROM documents ORDER BY id") == [("posted",)] * 3
    page = client.get("/ledger")
    assert page.status_code == 200
    assert "+20" in page.text and "+10" in page.text and "-15" in page.text
    assert "低库存" in client.get("/?low=1").text


def test_oversale_keeps_draft_and_no_partial_writes(client):
    initial_stock(client, quantity="15")
    record_id = document_id(draft(client, "sale", "16"))
    res = confirm(client, record_id)
    assert res.status_code == 400 and "库存不足" in res.text
    assert balance() == 15
    assert rows("SELECT state, posted_at FROM documents WHERE id=?", (record_id,)) == [
        ("draft", None)
    ]
    assert len(rows("SELECT * FROM stock_ledger")) == 1


def test_repeated_confirmation_does_not_repeat_deduction(client):
    initial_stock(client)
    record_id = document_id(draft(client, "sale", "5"))
    for _ in range(3):
        assert confirm(client, record_id).status_code == 302
    assert balance() == 15
    assert rows("SELECT COUNT(*) FROM stock_ledger WHERE document_id=?", (record_id,)) == [(1,)]


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc", "", " ", "1e2", "2147483648"])
def test_illegal_document_quantities_rejected(client, value):
    product(client)
    assert draft(client, quantity=value).status_code == 400
    assert not rows("SELECT * FROM documents")
    assert balance() == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"product_id": "999"},
        {"product_id": "abc"},
        {"kind": "transfer"},
        {"business_date": "2026-02-30"},
        {"business_date": "20261013"},
        {"note": "x" * 501},
    ],
)
def test_invalid_document_fields_rejected(client, changes):
    product(client)
    assert draft(client, **changes).status_code == 400
    assert not rows("SELECT * FROM documents")


def test_draft_edit_changes_no_stock_and_posted_document_readonly(client):
    product(client)
    record_id = document_id(draft(client))
    data = {
        "kind": "opening",
        "product_id": "1",
        "quantity": "25",
        "business_date": "2026-10-12",
        "note": "调整草稿",
    }
    assert client.post(f"/documents/{record_id}", data=data).status_code == 302
    assert balance() == 0 and not rows("SELECT * FROM stock_ledger")
    assert confirm(client, record_id).status_code == 302
    before = rows("SELECT * FROM documents"), rows("SELECT * FROM stock_ledger")
    data["quantity"] = "99"
    assert client.post(f"/documents/{record_id}", data=data).status_code == 400
    assert (rows("SELECT * FROM documents"), rows("SELECT * FROM stock_ledger")) == before
    assert "只读" in client.get(f"/documents/{record_id}").text
    assert not BeautifulSoup(client.get(f"/documents/{record_id}").text, "html.parser").find(
        "form"
    )


def test_zero_stock_and_equal_threshold_reminder(client):
    product(client, safety_stock="0")
    assert "BOOK-001" in client.get("/?low=1").text
    initial_id = document_id(draft(client))
    confirm(client, initial_id)
    assert "BOOK-001" not in client.get("/?low=1").text
    client.post(
        "/products/1/edit",
        data={"code": "BOOK-001", "name": "方格笔记本", "unit": "本", "safety_stock": "20"},
    )
    assert "BOOK-001" in client.get("/?low=1").text
    assert balance() == 20


@pytest.mark.parametrize(
    "changes",
    [
        {"safety_stock": "-1"},
        {"safety_stock": "1.5"},
        {"code": " "},
        {"name": ""},
        {"unit": ""},
        {"balance": "100"},
        {"prod_quantity": "100"},
    ],
)
def test_product_fields_and_direct_balance_changes_rejected(client, changes):
    assert product(client, **changes).status_code == 400
    assert not rows("SELECT * FROM catalog")


def test_duplicate_code_and_name_are_friendly(client):
    product(client)
    assert "编码已存在" in product(client, name="另一个商品").text
    assert "名称已存在" in product(client, code="BOOK-002").text
    assert len(rows("SELECT * FROM catalog")) == 1


def test_historical_product_deactivation_and_snapshot(client):
    record_id = initial_stock(client)
    assert (
        client.post(
            "/products/1/edit",
            data={"code": "BOOK-001", "name": "新名称", "unit": "本", "safety_stock": "2"},
        ).status_code
        == 302
    )
    assert rows("SELECT product_name FROM documents WHERE id=?", (record_id,)) == [("方格笔记本",)]
    assert (
        client.post(
            "/products/1/edit",
            data={"code": "BOOK-001", "name": "新名称", "unit": "箱", "safety_stock": "2"},
        ).status_code
        == 400
    )
    sale_id = document_id(draft(client, "sale", "1"))
    assert client.post("/products/1/delete").status_code == 302
    assert rows("SELECT active FROM catalog WHERE id=1") == [(0,)]
    assert balance() == 20
    assert draft(client, "purchase", "1").status_code == 400
    assert confirm(client, sale_id).status_code == 400
    assert len(rows("SELECT * FROM stock_ledger")) == 1
    assert "新名称" in client.get("/").text
    assert "BOOK-001" not in client.get("/?low=1").text


def test_multi_digit_delete_and_get_cannot_modify(client):
    for i in range(1, 11):
        product(client, code=f"P{i}", name=f"商品{i}")
    assert client.get("/products/10/delete").status_code == 405
    assert client.post("/products/10/delete").status_code == 302
    assert not rows("SELECT * FROM catalog WHERE id=10")


def test_form_protection_is_active_for_business_routes(client):
    initial_stock(client)
    record_id = document_id(draft(client, "sale", "1"))
    outsider = module.app.test_client()
    for path, data in [
        ("/products", {"code": "BAD", "name": "Bad", "unit": "本"}),
        ("/products/1/delete", {}),
        ("/products/1/edit", {}),
        ("/documents", {}),
        (f"/documents/{record_id}", {}),
        (f"/documents/{record_id}/confirm", {}),
    ]:
        assert outsider.post(path, data=data).status_code == 400
    assert balance() == 20
    page = outsider.get(f"/documents/{record_id}")
    soup = BeautifulSoup(page.text, "html.parser")
    token = soup.find("input", {"name": "csrf_token"})["value"]
    assert (
        outsider.post(f"/documents/{record_id}/confirm", data={"csrf_token": token}).status_code
        == 302
    )
    assert balance() == 19


def test_database_guards_posted_documents_and_ledger(client):
    initial_stock(client)
    statements = [
        "UPDATE documents SET quantity=99 WHERE id=1",
        "DELETE FROM documents WHERE id=1",
        "UPDATE stock_ledger SET change_quantity=99 WHERE id=1",
        "DELETE FROM stock_ledger WHERE id=1",
    ]
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError):
            with module.database() as conn:
                conn.execute(statement)
    assert balance() == 20


def test_confirmation_rolls_back_state_if_ledger_insert_fails(client):
    product(client)
    record_id = document_id(draft(client))
    with module.database() as conn:
        conn.execute(
            "CREATE TRIGGER forced_failure BEFORE INSERT ON stock_ledger BEGIN SELECT RAISE(ABORT, 'forced test failure'); END"
        )
    assert confirm(client, record_id).status_code == 409
    assert rows("SELECT state, posted_at FROM documents WHERE id=?", (record_id,)) == [
        ("draft", None)
    ]
    assert not rows("SELECT * FROM stock_ledger")
    assert balance() == 0


def test_opening_can_be_posted_once_and_only_before_other_stock(client):
    initial_stock(client)
    second = document_id(draft(client))
    assert confirm(client, second).status_code == 400
    assert balance() == 20
    product(client, code="P2", name="第二商品")
    purchase = document_id(draft(client, "purchase", "3", product_id="2"))
    assert confirm(client, purchase).status_code == 302
    late_opening = document_id(draft(client, product_id="2"))
    assert confirm(client, late_opening).status_code == 400
    assert balance(2) == 3


def test_two_concurrent_sales_cannot_overdraw(client):
    initial_stock(client, quantity="10")
    identifiers = [document_id(draft(client, "sale", "6")) for _ in range(2)]

    def post(record_id):
        independent = module.app.test_client()
        with independent.session_transaction() as session:
            session["form_token"] = "thread-token"
        return independent.post(
            f"/documents/{record_id}/confirm", data={"csrf_token": "thread-token"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(post, identifiers))
    assert sorted(statuses) == [302, 400]
    assert balance() == 4
    assert rows("SELECT COUNT(*) FROM stock_ledger") == [(2,)]


def test_concurrent_confirmation_of_same_document_posts_once(client):
    initial_stock(client)
    record_id = document_id(draft(client, "sale", "5"))

    def post(_):
        independent = module.app.test_client()
        with independent.session_transaction() as session:
            session["form_token"] = "thread-token"
        return independent.post(
            f"/documents/{record_id}/confirm", data={"csrf_token": "thread-token"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(post, range(2))) == [302, 302]
    assert balance() == 15
    assert rows("SELECT COUNT(*) FROM stock_ledger WHERE document_id=?", (record_id,)) == [(1,)]


def test_fresh_process_retains_stock_and_documents(client):
    initial_stock(client)
    purchase = document_id(draft(client, "purchase", "10"))
    confirm(client, purchase)
    sale = document_id(draft(client, "sale", "15"))
    confirm(client, sale)
    environment = os.environ.copy()
    environment["DATABASE_NAME"] = str(module.DATABASE_NAME)
    script = """
from inventory.app import app, database, stock
app.init_db()
client = app.test_client()
assert client.get('/').status_code == 200
with database() as conn:
    assert stock(conn, 1) == 15
    assert conn.execute('SELECT COUNT(*) FROM stock_ledger').fetchone()[0] == 3
print('fresh-process: balance=15 ledger=3')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=20
    )
    assert completed.returncode == 0, completed.stderr
    assert "fresh-process: balance=15 ledger=3" in completed.stdout


def test_legacy_balance_and_multiwarehouse_routes_not_exposed(client):
    for route in ("/product", "/location", "/movement", "/edit", "/delete"):
        assert client.get(route).status_code == 404
        assert client.post(route, data={"prod_quantity": "999"}).status_code == 404


def test_pages_and_empty_states(client):
    for route in ("/", "/stock", "/products", "/documents", "/ledger"):
        assert client.get(route).status_code == 200
    product(client)
    record_id = document_id(draft(client))
    for route in ("/products/1/edit", f"/documents/{record_id}"):
        assert client.get(route).status_code == 200
    assert client.get("/documents/999").status_code == 400
    assert client.post("/documents/999/confirm").status_code == 400
