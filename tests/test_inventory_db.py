from inventory.db import current_stock, init_db, recent_movements, record_movement


def test_record_movement_creates_item_and_stock(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    record_movement(db_path, "볼트 M8", 100, memo="초기입고")

    stock = current_stock(db_path)
    assert len(stock) == 1
    assert stock[0].item.name == "볼트 M8"
    assert stock[0].quantity == 100
    assert stock[0].item.unit == "개"


def test_stock_accumulates_across_movements(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    record_movement(db_path, "너트 M8", 50)
    record_movement(db_path, "너트 M8", -20, memo="출고")
    record_movement(db_path, "너트 M8", 10)

    stock = current_stock(db_path)
    assert stock[0].quantity == 40


def test_different_items_tracked_separately(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    record_movement(db_path, "볼트 M8", 100)
    record_movement(db_path, "너트 M8", 30)

    stock_by_name = {s.item.name: s.quantity for s in current_stock(db_path)}
    assert stock_by_name == {"볼트 M8": 100, "너트 M8": 30}


def test_recent_movements_returns_newest_first(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    record_movement(db_path, "볼트 M8", 100, memo="첫번째")
    record_movement(db_path, "볼트 M8", -10, memo="두번째")

    movements = recent_movements(db_path)
    assert [m.memo for m in movements] == ["두번째", "첫번째"]


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)
    init_db(db_path)  # 두 번 호출해도 에러 없이 통과해야 함

    assert current_stock(db_path) == []
