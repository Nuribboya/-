"""leadradar 설정: 우리 회사 프로필과, 사업영역이 겹치면 안 되는 기존 원청 정보."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class CompanyProfile:
    name: str
    business_description: str


@dataclass
class LeadRadarConfig:
    own_company: CompanyProfile
    excluded_client: CompanyProfile
    dart_api_key: Optional[str] = None
    g2b_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-5"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "LeadRadarConfig":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            own_company=CompanyProfile(**data["own_company"]),
            excluded_client=CompanyProfile(**data["excluded_client"]),
            dart_api_key=os.environ.get("DART_API_KEY"),
            g2b_api_key=os.environ.get("G2B_API_KEY"),
            anthropic_model=data.get("anthropic_model", "claude-sonnet-5"),
        )
