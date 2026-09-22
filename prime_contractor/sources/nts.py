"""국세청 사업자등록 상태조회 — 폐업·휴업한 곳을 걸러낸다.

신용등급은 유료라 못 가져오지만, 폐업 여부는 국세청이 무료로 연다.
낙찰 데이터에 사업자번호가 같이 오므로 추가 입력 없이 확인할 수 있다.

    https://api.odcloud.kr/api/nts-businessman/v1/status
    POST {"b_no": ["1234567890", ...]}   한 번에 100개까지

폐업한 곳은 아무리 점수가 높아도 연락할 이유가 없으니 목록에서 뺀다.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse

import requests

log = logging.getLogger(__name__)

STATUS_URL = "https://api.odcloud.kr/api/nts-businessman/v1/status"
BATCH_SIZE = 100                      # 명세상 한 번에 100건

#: 응답의 b_stt_cd. 01 계속사업자 / 02 휴업자 / 03 폐업자
CODE_LABEL = {"01": "계속사업자", "02": "휴업자", "03": "폐업자"}
DEAD_CODES = {"03"}                   # 목록에서 빼는 상태
WARN_CODES = {"02"}                   # 남기되 주의를 붙이는 상태


class NtsError(RuntimeError):
    pass


def clean_bizno(value: str) -> str:
    """'123-45-67890' → '1234567890'. 10자리가 아니면 빈 문자열."""
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) == 10 else ""


class NtsClient:
    def __init__(self, service_key: str, timeout: float = 20.0,
                 sleep_sec: float = 0.1, session: requests.Session | None = None) -> None:
        if not service_key:
            raise NtsError("국세청 조회용 인증키가 없습니다.")
        # 포털이 주는 Encoding 키를 그대로 넣어도 이중 인코딩되지 않게 한 번 푼다.
        self.key = urllib.parse.unquote(service_key) if "%" in service_key else service_key
        self.timeout = timeout
        self.sleep_sec = sleep_sec
        self.session = session or requests.Session()

    def statuses(self, biznos: list[str]) -> dict[str, dict]:
        """사업자번호 → {'code', 'label', 'closed_at'}. 못 읽은 번호는 빠진다."""
        wanted = [b for b in dict.fromkeys(clean_bizno(b) for b in biznos) if b]
        found: dict[str, dict] = {}

        for i in range(0, len(wanted), BATCH_SIZE):
            batch = wanted[i:i + BATCH_SIZE]
            try:
                response = self.session.post(
                    STATUS_URL, params={"serviceKey": self.key},
                    json={"b_no": batch},
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                # 상태 확인은 부가 정보다. 실패해도 탐색 전체를 멈추지 않는다.
                log.warning("사업자 상태조회 실패(계속 진행): %s", exc)
                continue
            time.sleep(self.sleep_sec)

            for row in payload.get("data") or []:
                bizno = clean_bizno(str(row.get("b_no", "")))
                if not bizno:
                    continue
                code = str(row.get("b_stt_cd") or "").strip()
                label = str(row.get("b_stt") or "").strip() or CODE_LABEL.get(code, "")
                if not code and not label:
                    continue          # 국세청에 등록되지 않은 번호
                found[bizno] = {"code": code, "label": label,
                                "closed_at": str(row.get("end_dt") or "").strip()}
        return found


def apply_statuses(candidates, statuses: dict[str, dict]) -> tuple[int, int]:
    """조회 결과를 후보에 붙인다. (확인된 수, 폐업으로 표시된 수)."""
    checked = dead = 0
    for cand in candidates:
        info = statuses.get(clean_bizno(cand.bizno))
        if not info:
            continue
        checked += 1
        cand.business_status = info["label"] or CODE_LABEL.get(info["code"], "")
        if info["code"] in DEAD_CODES:
            dead += 1
            cand.business_closed = True
            when = f" ({info['closed_at']})" if info["closed_at"] else ""
            cand.status_note = f"국세청 확인: 폐업{when}"
        elif info["code"] in WARN_CODES:
            cand.status_note = "국세청 확인: 휴업 중 — 연락 전에 확인하세요"
    return checked, dead
