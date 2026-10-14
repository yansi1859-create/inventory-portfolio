"""Regression coverage for the defects reproduced during project analysis."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from bs4 import BeautifulSoup

import inventory.legacy as module


@pytest.fixture
def client(monkeypatch, tmp_path):
    path = tmp_path / "regression.sqlite"
    monkeypatch.setattr(module, "DATABASE_NAME", str(path))
    module.app.init_db()
    client = module.app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "regression-token"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "regression-token"
    yield client
    # On Windows this also verifies every request released its file handle.
    path.unlink()


def rows(sql, parameters=()):
    with module.database() as conn:
        return conn.execute(sql, parameters).fetchall()


def seed(client):
    assert (
        client.post("/product", data={"prod_name": "Notebook", "prod_quantity": "10"}).status_code
        == 302
    )
    for name in ("A", "B"):
        assert client.post("/location", data={"warehouse_name": name}).status_code == 302


def move(client, **changes):
    data = {"prod_name": "Notebook", "from_loc": "", "to_loc": "A", "quantity": "6"}
    data.update(changes)
    return client.post("/movement", data=data)


@pytest.mark.parametrize(
    "value", ["0", "-1", "1.5", "abc", "", " ", "1e2", "2147483648", "9" * 5000]
)
def test_product_quantity_is_positive_integer(client, value):
    res = client.post("/product", data={"prod_name": "Notebook", "prod_quantity": value})
    assert res.status_code == 400
    assert not rows("SELECT * FROM products")


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc", "", "2147483648"])
def test_movement_quantity_is_positive_integer(client, value):
    seed(client)
    assert move(client, quantity=value).status_code == 400
    assert not rows("SELECT * FROM logistics")
    assert rows("SELECT unallocated_quantity FROM products") == [(10,)]


def test_overdraw_rolls_back_and_valid_transfers_balance(client):
    seed(client)
    assert move(client, quantity="11").status_code == 400
    assert move(client).status_code == 302
    before = rows("SELECT * FROM products"), rows("SELECT * FROM logistics")
    res = move(client, from_loc="A", to_loc="", quantity="20")
    assert res.status_code == 400
    assert "库存不足" in res.text
    assert (rows("SELECT * FROM products"), rows("SELECT * FROM logistics")) == before
    assert move(client, from_loc="A", to_loc="B", quantity="2").status_code == 302
    assert move(client, from_loc="B", to_loc="", quantity="2").status_code == 302
    with module.database() as conn:
        assert module.get_warehouse_data(conn, [(1,)], [(1,), (2,)]) == [
            ("Notebook", "A", 4),
            ("Notebook", "B", 0),
        ]
    assert rows("SELECT prod_quantity, unallocated_quantity FROM products") == [(10, 6)]


@pytest.mark.parametrize(
    "changes",
    [
        {"prod_name": "missing"},
        {"to_loc": "missing"},
        {"from_loc": "missing"},
        {"from_loc": "A", "to_loc": "A"},
        {"from_loc": "", "to_loc": ""},
    ],
)
def test_invalid_references_are_rejected_without_writes(client, changes):
    seed(client)
    assert move(client, **changes).status_code == 400
    assert not rows("SELECT * FROM logistics")
    assert rows("SELECT unallocated_quantity FROM products") == [(10,)]


def test_duplicate_names_are_friendly_and_rollback(client):
    seed(client)
    for path, data in [
        ("/product", {"prod_name": " Notebook ", "prod_quantity": "1"}),
        ("/location", {"warehouse_name": " A "}),
    ]:
        res = client.post(path, data=data)
        assert res.status_code == 409
        assert "名称已存在" in res.text
    assert len(rows("SELECT * FROM products")) == 1
    assert len(rows("SELECT * FROM location")) == 2


def test_multi_digit_ids_can_be_deleted_only_by_post(client):
    for i in range(1, 11):
        client.post("/product", data={"prod_name": f"P{i}", "prod_quantity": "1"})
        client.post("/location", data={"warehouse_name": f"L{i}"})
    assert client.get("/delete?type=product&prod_id=10").status_code == 405
    assert client.get("/delete?type=location&loc_id=10").status_code == 405
    assert client.post("/delete?type=product", data={"prod_id": "10"}).status_code == 302
    assert client.post("/delete?type=location", data={"loc_id": "10"}).status_code == 302
    assert not rows("SELECT * FROM products WHERE prod_id=10")
    assert not rows("SELECT * FROM location WHERE loc_id=10")


def test_history_causes_deactivation_and_preserves_balances(client):
    seed(client)
    assert move(client).status_code == 302
    before = rows("SELECT * FROM logistics")
    client.post("/delete?type=location", data={"loc_id": "1"})
    assert rows("SELECT active FROM location WHERE loc_id=1") == [(0,)]
    assert move(client).status_code == 400
    client.post("/delete?type=product", data={"prod_id": "1"})
    assert rows("SELECT active, prod_quantity, unallocated_quantity FROM products") == [(0, 10, 4)]
    assert rows("SELECT * FROM logistics") == before
    assert move(client, to_loc="B").status_code == 400
    assert "已停用" in client.get("/product").text
    assert "已停用" in client.get("/location").text


def test_edit_validation_rolls_back_name_and_quantity_together(client):
    seed(client)
    for value in ("-1", "0", "1.5", "abc", "5"):
        res = client.post(
            "/edit?type=product",
            data={"prod_id": "1", "prod_name": "Changed", "prod_quantity": value},
        )
        assert res.status_code == 400
        assert rows("SELECT prod_name, prod_quantity FROM products") == [("Notebook", 10)]
    for path, data in [
        ("/edit?type=product", {"prod_id": "999", "prod_name": "Missing"}),
        ("/edit?type=location", {"loc_id": "999", "loc_name": "Missing"}),
        ("/delete?type=product", {"prod_id": "999"}),
    ]:
        assert client.post(path, data=data).status_code == 400


def test_form_protection_rejects_missing_wrong_and_foreign_session_tokens(client):
    seed(client)
    untrusted = module.app.test_client()
    for token in ("", "wrong", "中文"):
        res = untrusted.post("/delete?type=product", data={"prod_id": "1", "csrf_token": token})
        assert res.status_code == 400
    # A real HTML form carries a token bound to that client's session.
    page = untrusted.get("/product")
    soup = BeautifulSoup(page.text, "html.parser")
    token = soup.find("input", {"name": "csrf_token"})["value"]
    assert (
        untrusted.post(
            "/delete?type=product", data={"prod_id": "1", "csrf_token": "regression-token"}
        ).status_code
        == 400
    )
    assert (
        untrusted.post(
            "/delete?type=product", data={"prod_id": "1", "csrf_token": token}
        ).status_code
        == 302
    )


def test_all_mutating_forms_include_protection(client):
    seed(client)
    for path in ("/product", "/location", "/movement"):
        soup = BeautifulSoup(client.get(path).text, "html.parser")
        for form in soup.find_all("form"):
            assert form.get("method", "").lower() == "post"
            assert form.find("input", {"name": "csrf_token"})


def test_database_connections_close_and_rollback_on_error(client, monkeypatch):
    original_connect = sqlite3.connect
    opened = []

    def capture(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(module.sqlite3, "connect", capture)
    seed(client)
    move(client, quantity="100")
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_simultaneous_allocations_cannot_overdraw(client):
    seed(client)

    def allocate():
        independent_client = module.app.test_client()
        with independent_client.session_transaction() as session:
            session["csrf_token"] = "thread-token"
        return independent_client.post(
            "/movement",
            data={
                "prod_name": "Notebook",
                "from_loc": "",
                "to_loc": "A",
                "quantity": "6",
                "csrf_token": "thread-token",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: allocate(), range(2)))
    assert sorted(statuses) == [302, 400]
    assert rows("SELECT unallocated_quantity FROM products") == [(4,)]
    assert len(rows("SELECT * FROM logistics")) == 1
