from fastapi.testclient import TestClient

from inventory.webapp.main import app

client = TestClient(app)


def test_index_page_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_DB", str(tmp_path / "inventory.db"))
    r = client.get("/")
    assert r.status_code == 200
    assert "재고 관리" in r.text


def test_create_movement_then_shows_in_stock(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_DB", str(tmp_path / "inventory.db"))

    r = client.post(
        "/movements",
        data={"item_name": "볼트 M8", "direction": "in", "quantity": "50", "unit": "개", "memo": "초기입고"},
        follow_redirects=True,
    )

    assert r.status_code == 200
    assert "볼트 M8" in r.text
    assert "50" in r.text
    assert "초기입고" in r.text


def test_outgoing_movement_reduces_stock(tmp_path, monkeypatch):
    db_path = str(tmp_path / "inventory.db")
    monkeypatch.setenv("INVENTORY_DB", db_path)

    client.post("/movements", data={"item_name": "너트 M8", "direction": "in", "quantity": "30", "unit": "개"})
    r = client.post(
        "/movements",
        data={"item_name": "너트 M8", "direction": "out", "quantity": "10", "unit": "개"},
        follow_redirects=True,
    )

    assert r.status_code == 200
    assert "20" in r.text
