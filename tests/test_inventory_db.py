from inventory.db import (
    current_stock,
    init_db,
    recent_movements,
    record_movement,
    set_lead_time_days,
    stock_outlook,
)


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


def test_stock_outlook_estimates_days_until_depletion(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    record_movement(db_path, "볼트 M8", 300, memo="입고")
    record_movement(db_path, "볼트 M8", -30, memo="출고")  # 최근 30일 평균 하루 1개 소비

    outlook = stock_outlook(db_path, lookback_days=30)

    assert outlook[0].item.name == "볼트 M8"
    assert outlook[0].quantity == 270
    assert outlook[0].avg_daily_consumption == 1.0
    assert outlook[0].days_until_depletion == 270.0


def test_stock_outlook_none_when_no_outgoing_history(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    record_movement(db_path, "너트 M8", 50, memo="입고만 있음")

    outlook = stock_outlook(db_path)

    assert outlook[0].avg_daily_consumption == 0.0
    assert outlook[0].days_until_depletion is None
    assert outlook[0].needs_reorder is False


def test_stock_outlook_flags_reorder_when_depletion_within_lead_time(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    # 리드타임 10일짜리 핵심 자재: 현재 5개 남았고, 최근 30일간 하루 평균 1개씩 소비
    # -> 5일 뒤 소진 예상인데 리드타임(10일)보다 빨라서 지금 발주해야 함
    record_movement(db_path, "차단기", 35, lead_time_days=10)
    record_movement(db_path, "차단기", -30, memo="출고")

    outlook = stock_outlook(db_path, lookback_days=30)

    assert outlook[0].quantity == 5
    assert outlook[0].item.lead_time_days == 10
    assert outlook[0].days_until_depletion == 5.0
    assert outlook[0].needs_reorder is True


def test_stock_outlook_no_reorder_when_depletion_beyond_lead_time(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)

    # 리드타임 3일인데, 소진까지 270일이나 남아있어 발주 필요 없음
    record_movement(db_path, "차단기", 300, lead_time_days=3)
    record_movement(db_path, "차단기", -30, memo="출고")

    outlook = stock_outlook(db_path, lookback_days=30)

    assert outlook[0].needs_reorder is False


def test_set_lead_time_days_updates_existing_item(tmp_path):
    db_path = tmp_path / "inventory.db"
    init_db(db_path)
    record_movement(db_path, "차단기", 10)

    set_lead_time_days(db_path, "차단기", 14)

    outlook = stock_outlook(db_path)
    assert outlook[0].item.lead_time_days == 14
