import fs from 'node:fs';
import path from 'node:path';

/** Append-only event log (data/journal.jsonl): deaths, damage, skill results, milestones. */
export class Journal {
  constructor(dataDir) {
    this.file = path.join(dataDir, 'journal.jsonl');
    fs.mkdirSync(dataDir, { recursive: true });
    this.entries = [];
    try {
      this.entries = fs
        .readFileSync(this.file, 'utf8')
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l));
    } catch {}
  }

  add(type, data = {}) {
    const entry = { t: new Date().toISOString(), type, ...data };
    this.entries.push(entry);
    fs.appendFileSync(this.file, JSON.stringify(entry) + '\n');
    return entry;
  }

  of(type) {
    return this.entries.filter((e) => e.type === type);
  }

  /** Group failures since entry index `since`: { bySkill: {name: {n, errors[]}}, deaths: [causes] } */
  failureDigest(since = 0) {
    const bySkill = {};
    const deaths = [];
    for (const e of this.entries.slice(since)) {
      if (e.type === 'skill_fail') {
        const s = (bySkill[e.name] ??= { n: 0, errors: [] });
        s.n++;
        if (!s.errors.includes(e.error) && s.errors.length < 4) s.errors.push(e.error);
      } else if (e.type === 'death') deaths.push(e.cause ?? '원인불명');
    }
    return { bySkill, deaths };
  }

  /** "죽음 3회: zombie 2, lava 1 / 밤에 2회" */
  deathSummary() {
    const deaths = this.of('death');
    if (!deaths.length) return '';
    const by = {};
    let night = 0;
    for (const d of deaths) {
      by[d.cause ?? '원인불명'] = (by[d.cause ?? '원인불명'] ?? 0) + 1;
      if (d.night) night++;
    }
    const causes = Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(', ');
    return `죽음 ${deaths.length}회: ${causes} / 밤에 ${night}회`;
  }

  /** Compact recent history for the prompt. */
  recentText(n = 8) {
    const fmt = (e) => {
      const time = e.t.slice(11, 16);
      switch (e.type) {
        case 'death': return `${time} 💀 ${e.cause ?? '원인불명'} (${e.where}, ${e.night ? '밤' : '낮'}, 직전: ${e.lastAction})`;
        case 'damage': return `${time} 🩸 -${e.amount} 체력 (${e.source ?? '?'})`;
        case 'skill_ok': return `${time} ✅ ${e.name}`;
        case 'skill_fail': return `${time} ❌ ${e.name}: ${e.error}`;
        case 'new_skill': return `${time} 🎓 ${e.name}`;
        case 'milestone': return `${time} 🏁 ${e.id}`;
        case 'advancement': return `${time} 🏆 발전 과제: ${e.title}`;
        case 'self_goal': return `${time} 🎯 새 목표: ${e.goal}`;
        case 'self_goal_done': return `${time} 🏁 목표 달성: ${e.goal}`;
        case 'self_goal_dropped': return `${time} 🏳️ 목표 포기: ${e.goal}`;
        case 'consolidate': return `${time} 🧹 정리: ${e.archived?.length ?? 0}개 보관`;
        default: return `${time} ${e.type}`;
      }
    };
    return this.entries.slice(-n).map(fmt).join('\n');
  }

  report() {
    const lines = [`총 기록 ${this.entries.length}개`];
    const sum = this.deathSummary();
    if (sum) lines.push(sum);
    lines.push('\n[마일스톤]');
    for (const e of this.of('milestone')) lines.push(`  ${e.t.slice(0, 16).replace('T', ' ')}  ${e.id}`);
    lines.push('\n[죽음]');
    for (const e of this.of('death'))
      lines.push(`  ${e.t.slice(0, 16).replace('T', ' ')}  ${e.cause ?? '원인불명'} @ ${e.where} (${e.night ? '밤' : '낮'}) 직전행동: ${e.lastAction}`);
    lines.push('\n[발전 과제]');
    for (const e of this.of('advancement')) lines.push(`  ${e.t.slice(0, 16).replace('T', ' ')}  ${e.title}`);
    lines.push('\n[새로 배운 스킬]');
    for (const e of this.of('new_skill')) lines.push(`  ${e.t.slice(0, 16).replace('T', ' ')}  ${e.name}`);
    return lines.join('\n');
  }
}
