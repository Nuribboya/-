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
    for (const [name, s] of Object.entries(SEEDS)) {
      if (!this.index[name]) this.#write(name, s.description, s.args, s.code, true);
    }
  }

  #write(name, description, args, code, builtin = false) {
    fs.writeFileSync(path.join(this.dir, `${name}.js`), code);
    const prev = this.index[name];
    this.index[name] = { description, args, uses: prev?.uses ?? 0, fails: prev?.fails ?? 0, streak: 0, builtin };
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

  save(name, description, args, code) {
    if (!NAME_RE.test(name)) throw new Error(`bad skill name: ${name}`);
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
