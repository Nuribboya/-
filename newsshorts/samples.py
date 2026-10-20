"""Fictional demo articles for trying the pipeline without live feeds."""
from newsshorts.sources import NewsItem

SAMPLE_ITEM = NewsItem(
    id="demo",
    source="데모뉴스",
    title="국회, 내년도 예산안 처리… 기초연금 월 2만 원 인상 포함",
    summary=(
        "국회는 오늘 본회의를 열고 내년도 예산안을 처리했다. "
        "예산안에는 65세 이상 기초연금 수급자의 월 지급액을 2만 원 올리는 내용이 담겼다. "
        "인상분은 내년 1월 지급분부터 적용된다. "
        "여당 원내대표는 '어르신 생활 안정을 위한 예산'이라고 말했다."
    ),
    link="https://example.com/news/demo",
)

SAMPLE_US_ITEM = NewsItem(
    id="demo-us",
    source="데모뉴스(미국)",
    title="Exclusive: White House weighing new tariffs on Korean steel, officials say",
    summary=(
        "The administration is considering raising tariffs on steel imports from South Korea, "
        "two officials familiar with the discussions said. No final decision has been made."
    ),
    link="https://example.com/news/demo-us",
    region="us",
)
