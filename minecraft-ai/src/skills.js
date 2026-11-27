import fs from 'node:fs';
import path from 'node:path';

const NAME_RE = /^[a-z][a-z0-9_]{2,40}$/;

const SEEDS = {
  collect_logs: {
    description: '주변에서 원목을 count개 캔다 (기본 5)',
    args: { count: 5 },
    code: `const n = args.count ?? 5;\nawait api.gather('log', n);\nreturn api.inventory();`,
  },
  craft_item: {
    description: '아이템을 제작한다. 필요하면 근처 작업대를 자동 사용 (item 이름, count=제작 횟수)',
    args: { item: 'oak_planks', count: 1 },
    code: `await api.craft(args.item, args.count ?? 1);\nreturn api.inventory();`,
  },
  place_block: {
    description: '인벤토리의 블록을 주변 땅에 설치한다 (예: crafting_table, furnace)',
    args: { block: 'crafting_table' },
    code: `await api.place(args.block);\nreturn 'placed';`,
  },
  eat_food: {
    description: '가진 음식 중 가장 좋은 것을 먹는다',
    args: {},
    code: `await api.eat();\nreturn { food: api.food() };`,
  },
  explore_area: {
    description: '새 지형을 찾아 랜덤 방향으로 distance칸 이동한다 (기본 30)',
    args: { distance: 30 },
    code: `await api.explore(args.distance ?? 30);\nreturn api.position();`,
  },
};

/** On-disk skill library: data/skills/<name>.js (function body) + index.json (metadata). */
export class Skills {
  constructor(dataDir) {
    this.dir = path.join(dataDir, 'skills');
    this.indexFile = path.join(this.dir, 'index.json');
    fs.mkdirSync(this.dir, { recursive: true });
    try {
      this.index = JSON.parse(fs.readFileSync(this.indexFile, 'utf8'));
    } catch {
      this.index = {};
    }
    // Builtins are always present and always the original code (repairs an older overwrite).
    for (const [name, s] of Object.entries(SEEDS)) {
      if (!this.index[name]?.builtin) this.#write(name, s.description, s.args, s.code, true);
    }
  }

  #write(name, description, args, code, builtin = false) {
    fs.writeFileSync(path.join(this.dir, `${name}.js`), code);
    const prev = this.index[name];
    this.index[name] = { description, args, uses: prev?.uses ?? 0, fails: prev?.fails ?? 0, streak: 0, builtin, createdAt: prev?.createdAt ?? Date.now() };
    fs.writeFileSync(this.indexFile, JSON.stringify(this.index, null, 2));
  }

  static validName(name) {
    return NAME_RE.test(name);
  }

  has(name) {
    return !!this.index[name];
  }

  code(name) {
    return fs.readFileSync(path.join(this.dir, `${name}.js`), 'utf8');
  }

  static isBuiltin(name) {
    return Object.hasOwn(SEEDS, name);
  }

  save(name, description, args, code) {
    if (!NAME_RE.test(name)) throw new Error(`bad skill name: ${name}`);
    if (Skills.isBuiltin(name))
      throw new Error(`"${name}" is a builtin skill and cannot be overwritten - give the new skill a different name (it can call it via api.useSkill)`);
    this.#write(name, description, args, code);
  }

  record(name, ok) {
    const s = this.index[name];
    if (!s) return;
    if (ok) {
      s.uses++;
      s.streak = 0;
    } else {
      s.fails++;
      s.streak = (s.streak ?? 0) + 1;
    }
    fs.writeFileSync(this.indexFile, JSON.stringify(this.index, null, 2));
  }

  /** Move a skill out of the active library (kept in skills/archive/, not deleted). */
  archive(name) {
    const s = this.index[name];
    if (!s || s.builtin) return false;
    const dir = path.join(this.dir, 'archive');
    fs.mkdirSync(dir, { recursive: true });
    fs.renameSync(path.join(this.dir, `${name}.js`), path.join(dir, `${name}.js`));
    const idxFile = path.join(dir, 'index.json');
    let idx = {};
    try {
      idx = JSON.parse(fs.readFileSync(idxFile, 'utf8'));
    } catch {}
    idx[name] = { ...s, archivedAt: new Date().toISOString() };
    fs.writeFileSync(idxFile, JSON.stringify(idx, null, 2));
    delete this.index[name];
    fs.writeFileSync(this.indexFile, JSON.stringify(this.index, null, 2));
    return true;
  }

  /**
   * Keep the library lean: archive learned skills that never worked or keep failing,
   * then, if still over maxSkills, archive the lowest-scoring ones. Builtins are never touched.
   */
  consolidate(maxSkills = 25) {
    const archived = [];
    for (const [n, s] of Object.entries(this.index)) {
      if (s.builtin) continue;
      if ((s.uses === 0 && s.fails >= 2) || s.streak >= 5) {
        if (this.archive(n)) archived.push(n);
      }
    }
    const learned = Object.entries(this.index).filter(([, s]) => !s.builtin);
    if (learned.length > maxSkills) {
      const score = (s) => s.uses - s.fails;
      learned.sort((a, b) => score(a[1]) - score(b[1]) || (a[1].createdAt ?? 0) - (b[1].createdAt ?? 0));
      for (const [n] of learned.slice(0, learned.length - maxSkills)) if (this.archive(n)) archived.push(n);
    }
    return archived;
  }

  learnedCount() {
    return Object.values(this.index).filter((s) => !s.builtin).length;
  }

  /**
   * Best existing skill for a name the model made up, e.g. collect_logs_v2_fixed -> collect_logs.
   * Prefix match first, then word overlap. Returns null if nothing is close enough.
   */
  closest(name) {
    const names = Object.keys(this.index);
    const prefix = names.filter((n) => name.startsWith(n + '_') || n.startsWith(name + '_')).sort((a, b) => b.length - a.length);
    if (prefix.length) return prefix[0];
    const words = new Set(name.split('_').filter((w) => w.length > 2 && !/^v\d+$/.test(w)));
    let best = null;
    let bestScore = 0;
    for (const n of names) {
      const nw = n.split('_').filter((w) => w.length > 2);
      const common = nw.filter((w) => words.has(w)).length;
      const score = common / Math.max(nw.length, 1);
      if (score > bestScore) {
        best = n;
        bestScore = score;
      }
    }
    return bestScore >= 0.5 ? best : null;
  }

  count() {
    return Object.keys(this.index).length;
  }

  /** Compact list for the prompt. */
  describe() {
    return Object.entries(this.index)
      .map(([n, s]) => `- ${n}(${JSON.stringify(s.args)}): ${s.description} [성공 ${s.uses}/실패 ${s.fails}${s.streak >= 2 ? ', 연속실패 중 - 재작성 고려' : ''}]`)
      .join('\n');
  }
}
