from panel_inspection.bom import compare_to_bom, counts_from_detections, load_bom


def test_counts_from_detections():
    detections = [
        {"class": "차단기", "confidence": 0.9},
        {"class": "차단기", "confidence": 0.8},
        {"class": "릴레이", "confidence": 0.95},
    ]

    assert counts_from_detections(detections) == {"차단기": 2, "릴레이": 1}


def test_compare_to_bom_detects_missing_part():
    bom = {"차단기": 2, "릴레이": 4}
    detected = {"차단기": 2, "릴레이": 3}

    diff = compare_to_bom(detected, bom)

    assert diff.missing == {"릴레이": 1}
    assert diff.extra == {}
    assert diff.is_complete is False


def test_compare_to_bom_all_present_is_complete():
    bom = {"차단기": 2, "릴레이": 4}
    detected = {"차단기": 2, "릴레이": 4}

    diff = compare_to_bom(detected, bom)

    assert diff.is_complete is True


def test_compare_to_bom_flags_extra_and_unknown_parts():
    bom = {"차단기": 2}
    detected = {"차단기": 3, "알수없는부품": 1}

    diff = compare_to_bom(detected, bom)

    assert diff.missing == {}
    assert diff.extra == {"차단기": 1, "알수없는부품": 1}


def test_load_bom_reads_yaml(tmp_path):
    bom_path = tmp_path / "bom.yaml"
    bom_path.write_text("차단기: 2\n릴레이: 4\n", encoding="utf-8")

    bom = load_bom(bom_path)

    assert bom == {"차단기": 2, "릴레이": 4}
