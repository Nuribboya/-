from __future__ import annotations

from datetime import datetime
from html import escape

from resell_radar.models import KST, fmt_kst
from resell_radar.scoring import MAYBE, PASS, RECOMMEND

VERDICT_CLASS = {RECOMMEND: "rec", MAYBE: "maybe", PASS: "pass"}

# Self-contained page (no external fonts/scripts) so it opens offline from disk.
STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--text:#16181d;--muted:#6b7280;--line:#e5e7eb;
--rec:#0f9d58;--rec-bg:#e7f6ee;--maybe:#b7791f;--maybe-bg:#fdf3e1;--pass:#9ca3af;--pass-bg:#f1f2f4;
--up:#0f9d58;--down:#d14343;--accent:#111827;--accent-text:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--text:#eceef2;--muted:#9aa1ad;
--line:#2a2f38;--rec:#34c77b;--rec-bg:#123222;--maybe:#e0a84a;--maybe-bg:#33270f;--pass:#7c8492;
--pass-bg:#22262d;--up:#34c77b;--down:#f07272;--accent:#eceef2;--accent-text:#111}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:-apple-system,"Segoe UI","Malgun Gothic","Apple SD Gothic Neo",sans-serif;line-height:1.45}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
header h1{margin:0;font-size:24px}
header .sub{color:var(--muted);font-size:14px;margin-top:4px}
.stats{display:flex;gap:10px;margin:18px 0;flex-wrap:wrap}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 16px;min-width:96px}
.stat b{display:block;font-size:22px}
.stat span{color:var(--muted);font-size:13px}
.stat.rec b{color:var(--rec)}.stat.maybe b{color:var(--maybe)}.stat.pass b{color:var(--pass)}
.warn{background:var(--maybe-bg);color:var(--maybe);border-radius:10px;padding:10px 14px;margin-bottom:14px;font-size:14px}
.bar{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.tabs,.sort{display:flex;gap:6px;flex-wrap:wrap}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--text);
border-radius:999px;padding:6px 14px;cursor:pointer;font-size:14px}
button.on{background:var(--accent);color:var(--accent-text);border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;
display:flex;flex-direction:column;gap:10px;border-top:4px solid var(--pass)}
.card.rec{border-top-color:var(--rec)}.card.maybe{border-top-color:var(--maybe)}
.top{display:flex;align-items:center;gap:8px}
.badge{font-size:13px;font-weight:700;padding:3px 10px;border-radius:999px;background:var(--pass-bg);color:var(--pass)}
.rec .badge{background:var(--rec-bg);color:var(--rec)}.maybe .badge{background:var(--maybe-bg);color:var(--maybe)}
.grade{margin-left:auto;font-weight:800;font-size:20px;width:36px;height:36px;border-radius:10px;
display:grid;place-items:center;background:var(--bg);border:1px solid var(--line)}
.name{font-weight:700;font-size:16px}
.shop{color:var(--muted);font-size:13px}
.money{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.profit{font-size:26px;font-weight:800}
.profit.neg{color:var(--down)}
.est{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.meta{color:var(--muted);font-size:13px}
.time{font-size:14px;font-weight:600}
.time.urgent{color:var(--down)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{font-size:12px;padding:3px 8px;border-radius:6px;background:var(--bg);border:1px solid var(--line)}
.chip.up{color:var(--up)}.chip.down{color:var(--down)}
a.go{margin-top:auto;text-align:center;text-decoration:none;background:var(--accent);color:var(--accent-text);
border-radius:10px;padding:9px;font-weight:600;font-size:14px}
.empty{color:var(--muted);text-align:center;padding:40px 0}
"""

SCRIPT = """
const cards=[...document.querySelectorAll('.card')];
let filter='active',order='end';
function remain(card){
  const s=+card.dataset.start,e=+card.dataset.end,now=Date.now(),el=card.querySelector('.time');
  if(!el)return;
  const f=ms=>{const m=Math.floor(ms/60000),d=Math.floor(m/1440),h=Math.floor(m%1440/60);
    return d?`${d}일 ${h}시간`:h?`${h}시간 ${m%60}분`:`${m%60}분`};
  el.classList.remove('urgent');
  if(s&&now<s)el.textContent=`응모 시작까지 ${f(s-now)}`;
  else if(e&&now<e){el.textContent=`마감까지 ${f(e-now)}`;if(e-now<3*3600e3)el.classList.add('urgent')}
  else if(e)el.textContent='응모 마감됨';
}
function render(){
  const grid=document.querySelector('.grid');
  const shown=cards.filter(c=>filter==='all'||(filter==='active'?c.dataset.v!=='pass':c.dataset.v===filter));
  shown.sort((a,b)=>order==='profit'?(+b.dataset.profit)-(+a.dataset.profit)
    :(+a.dataset.end||9e15)-(+b.dataset.end||9e15));
  cards.forEach(c=>c.remove());shown.forEach(c=>grid.appendChild(c));
  document.querySelector('.empty').hidden=shown.length>0;
}
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('on',x===b));render()});
document.querySelectorAll('[data-order]').forEach(b=>b.onclick=()=>{order=b.dataset.order;
  document.querySelectorAll('[data-order]').forEach(x=>x.classList.toggle('on',x===b));render()});
cards.forEach(remain);setInterval(()=>cards.forEach(remain),30000);render();
"""


def _ms(dt: datetime | None) -> str:
    return str(int(dt.timestamp() * 1000)) if dt else ""


def _chip(reason: str) -> str:
    if "+1" in reason:
        cls = "chip up"
    elif "-1" in reason or "미달" in reason:
        cls = "chip down"
    else:
        cls = "chip"
    return f'<span class="{cls}">{escape(reason)}</span>'


def _card(result) -> str:
    rel, s = result.release, result.score
    cls = VERDICT_CLASS[s.verdict]
    if s.est_profit is not None:
        neg = " neg" if s.est_profit < 0 else ""
        ratio = f" · 수익률 {s.profit_ratio * 100:.0f}%" if s.profit_ratio is not None else ""
        est = '<span class="est">추정</span>' if s.price_is_estimate else '<span class="est">시세 기준</span>'
        money = (
            f'<div class="money"><span class="profit{neg}">{s.est_profit:+,}원</span>{est}</div>'
            f'<div class="meta">정가 {rel.retail_price:,}원 → 예상 {s.est_price:,}원{ratio}</div>'
        )
    else:
        money = '<div class="meta">정가 정보 없음</div>'
    when = ""
    if rel.entry_start or rel.entry_end:
        when = (
            f'<div><div class="time"></div>'
            f'<div class="meta">응모 {fmt_kst(rel.entry_start)} ~ {fmt_kst(rel.entry_end)}</div></div>'
        )
    shop = escape(rel.brand or rel.source)
    link = f'<a class="go" href="{escape(rel.url)}" target="_blank" rel="noopener">응모하러 가기</a>' if rel.url else ""
    return (
        f'<article class="card {cls}" data-v="{cls}" data-profit="{s.est_profit or 0}" '
        f'data-start="{_ms(rel.entry_start)}" data-end="{_ms(rel.entry_end)}">'
        f'<div class="top"><span class="badge">{s.verdict}</span><span class="shop">{shop}</span>'
        f'<span class="grade">{s.level}</span></div>'
        f'<div class="name">{escape(rel.name)}</div>{money}{when}'
        f'<div class="chips">{"".join(_chip(r) for r in s.reasons)}</div>{link}</article>'
    )


def render_html(results: list, errors: list[str], generated_at: datetime) -> str:
    counts = {v: sum(1 for r in results if r.score.verdict == v) for v in (RECOMMEND, MAYBE, PASS)}
    warn = "".join(f'<div class="warn">⚠ {escape(e)}</div>' for e in errors)
    stats = (
        f'<div class="stat rec"><b>{counts[RECOMMEND]}</b><span>추천</span></div>'
        f'<div class="stat maybe"><b>{counts[MAYBE]}</b><span>애매</span></div>'
        f'<div class="stat pass"><b>{counts[PASS]}</b><span>패스</span></div>'
        f'<div class="stat"><b>{len(results)}</b><span>전체</span></div>'
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>리셀 레이더</title><style>{STYLE}</style></head>
<body><div class="wrap">
<header><h1>리셀 레이더</h1>
<div class="sub">{generated_at.astimezone(KST).strftime("%Y-%m-%d %H:%M")} 기준 · 응모 가치가 있는 발매만 골라 보여줍니다</div></header>
<div class="stats">{stats}</div>{warn}
<div class="bar">
<div class="tabs"><button class="on" data-filter="active">추천+애매</button><button data-filter="rec">추천</button>
<button data-filter="maybe">애매</button><button data-filter="pass">패스</button><button data-filter="all">전체</button></div>
<div class="sort"><button class="on" data-order="end">마감 임박순</button><button data-order="profit">수익순</button></div>
</div>
<div class="grid">{"".join(_card(r) for r in results)}</div>
<div class="empty" hidden>해당하는 발매가 없어요</div>
</div><script>{SCRIPT}</script></body></html>"""
