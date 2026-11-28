import fs from 'node:fs';
import path from 'node:path';

const NAME_RE = /^[a-z][a-z0-9_]{2,59}$/;

const SEEDS = {
  collect_logs: {
    description: '주변에서 원목을 count개 캔다 (기본 10)',
    args: { count: 5 },
    code: `const n = args.count ?? 10;\nawait api.gather('log', n);\nreturn api.inventory();`,
  },
  craft_item: {
    description: '아이템을 제작한다. 필요하면 근처 작업대를 자동 사용 (item 이름, count=제작 횟수)',
    args: { item: 'oak_planks', count: 1 },
    code: `await api.craft(args.item ?? args.name ?? args.block, args.count ?? 1);\nreturn api.inventory();`,
  },
  place_block: {
    description: '인벤토리의 블록을 주변 땅에 설치한다 (예: crafting_table, furnace)',
    args: { block: 'crafting_table' },
    code: `await api.place(args.block ?? args.item ?? args.name);\nreturn 'placed';`,
  },
  follow_plan: {
    description: '자동 계획의 다음 단계들을 한 번에 연달아 실행한다 (가장 빠름). 자동 계획이 있으면 우선 사용',
    args: { steps: 4 },
    timeoutMul: 4,
    code: `return await api.followPlan(args.steps ?? 4);`,
  },
  equip_item: {
    description: '아이템 장착: slot = hand | off-hand | head | torso | legs | feet (갑옷 입기, 방패 들기, 칼 들기)',
    args: { item: 'iron_helmet', slot: 'head' },
    code: `await api.equip(args.item ?? args.name, args.slot ?? 'hand');\nreturn api.inventory();`,
  },
  mine_block: {
    description: '블록을 count개 캔다 (예: stone→조약돌, iron_ore→raw_iron, coal_ore→석탄; raw_iron처럼 아이템 이름도 됨). 맞는 곡괭이 필요',
    args: { block: 'stone', count: 3 },
    code: `await api.gather(args.block ?? args.item ?? args.ore ?? args.name, args.count ?? 1, 48, args.until ?? null);\nreturn api.inventory();`,
  },
  smelt_item: {
    description: '근처 화로에서 item을 count개 굽는다 (예: raw_iron). 연료는 가진 것 중 자동 선택 (fuel로 지정 가능)',
    args: { item: 'raw_iron', count: 1, fuel: 'coal' },
    code: `await api.smelt(args.item ?? args.input ?? args.name, args.count ?? 1, args.fuel);\nreturn api.inventory();`,
  },
  descend_mine: {
    description: '계단식으로 파 내려간다 (기본 y=-58 다이아 높이). 용암/물/낭떠러지가 보이면 멈춘다',
    args: { y: -58 },
    code: `const y = await api.descend(args.y ?? -58);\nreturn { y };`,
  },
  block_with_shield: {
    description: '왼손 방패를 ms 동안 들어 공격을 막는다 (방패를 왼손에 장착한 상태여야 함)',
    args: { ms: 3000 },
    passive: true,
    code: `await api.block(args.ms ?? args.time ?? 3000);\nreturn 'blocked';`,
  },
  block_arrows: {
    description: '가까운 스켈레톤을 바라보며 방패로 화살을 막는다 (방패가 없으면 장착부터). 스켈레톤이 없으면 에러',
    args: { seconds: 8 },
    passive: true,
    code: `return await api.blockArrows(args.seconds ?? 8);`,
  },
  fill_bucket: {
    description: '빈 양동이로 가까운 물(또는 용암) 원천을 뜬다 (liquid = water | lava)',
    args: { liquid: 'water' },
    code: `return await api.fillBucket(args.liquid ?? 'water');`,
  },
  get_obsidian: {
    description: '흑요석을 count개 얻는다: 근처 자연산을 캐고, 없으면 용암 웅덩이에 물을 부어 만들어서 캔다 (다이아 곡괭이 필요)',
    args: { count: 10 },
    timeoutMul: 4,
    code: `return await api.makeObsidian(args.count ?? 10);`,
  },
  build_nether_portal: {
    description: '평평한 곳에 네더 포탈 틀(흑요석 10 + 모서리 블록 4)을 짓고 부싯돌과 부시로 불을 붙인다 (들어가지는 않음)',
    args: {},
    timeoutMul: 3,
    code: `return await api.buildPortal();`,
  },
  unstuck: {
    description: '벽이나 구석에 끼었을 때 빠져나오기 (막힌 블록을 부수고 다른 방향으로 이동)',
    args: {},
    code: `return await api.unstuck();`,
  },
  recover_items: {
    description: '죽은 자리로 돌아가 떨어진 아이템을 줍는다 (죽고 5분 안에만 남아 있음)',
    args: { x: 0, y: 64, z: 0 },
    code: `return await api.recoverDrops(args.x, args.y, args.z);`,
  },
  go_to_surface: {
    description: '지하에서 지상으로 올라간다 (기본 y=70). 나무가 필요하거나 지하에 갇혔을 때',
    args: { y: 70 },
    code: `const y = await api.ascend(args.y ?? 70);\nreturn { y };`,
  },
  explore_cave: {
    description: '근처 동굴 안으로 들어가 둘러본다 (광석 찾기)',
    args: {},
    code: `return await api.exploreCave(32);`,
  },
  night_shelter: {
    description: '위급할 때 숨기: 땅을 3칸 파고 들어가 위를 막는다 (다음에 이동하면 알아서 파고 나감)',
    args: {},
    passive: true, // "already sheltered" is a fine result, not a failure
    code: `return await api.shelter();`,
  },
  wait_for_day: {
    description: '(보통은 쓰지 말 것) 은신처 안에서 아침까지 기다린다',
    args: {},
    passive: true,
    code: `for (let i = 0; i < 10 && api.isNight(); i++) await api.sleep(10000);\nreturn { night: api.isNight() };`,
  },
  hunt_animal: {
    description: '동물을 count마리 잡고 떨어진 아이템을 줍는다 (sheep→양털/양고기, cow→가죽/소고기, pig, chicken)',
    args: { mob: 'sheep', count: 1 },
    code: `for (let i = 0; i < (args.count ?? 1); i++) await api.attack(args.mob ?? 'sheep');\nreturn api.inventory();`,
  },
  sleep_in_bed: {
    description: '침대에서 자서 밤을 건너뛴다 (근처 침대 또는 인벤토리 침대 설치, 부활 지점도 침대로). 밤에도 작업할 거면 안 써도 됨',
    args: {},
    passive: true,
    code: `return await api.sleepInBed();`,
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
      const cur = this.index[name];
      let curCode = null;
      try {
        curCode = fs.readFileSync(path.join(this.dir, `${name}.js`), 'utf8');
      } catch {}
      if (!cur?.builtin || cur.description !== s.description || curCode !== s.code) this.#write(name, s.description, s.args, s.code, true);
      this.index[name].passive = !!s.passive;
      this.index[name].timeoutMul = s.timeoutMul ?? 1;
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

  /**
   * Names not worth writing at all, with the reason (or null if fine):
   * - copies of a builtin (smelt_item_v2, collect_logs_fixed): the builtin already works
   * - check/verify-only skills: they never change anything, and the prompt already has that info
   */
  static pointlessName(name) {
    const base = Object.keys(SEEDS).find((b) => name !== b && name.startsWith(b + '_'));
    if (base) return `"${name}" is a copy of the builtin skill "${base}", which already works - do not write it, run "${base}" (with args) instead`;
    if (/^(check|verify|inspect|confirm|is|has|get)_/.test(name))
      return `"${name}" only checks something - inventory and nearby blocks are already in the prompt. Only write skills that DO something (gather/craft/place/smelt/move/fight)`;
    return null;
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
  /** Compact list for the prompt: builtins first (they always work), then learned skills. */
  describe() {
    const line = ([n, s]) =>
      `- ${n}(${JSON.stringify(s.args)}): ${s.description}` +
      (s.builtin ? '' : ` [성공 ${s.uses}/실패 ${s.fails}${s.streak >= 2 ? ', 연속실패 중' : ''}]`);
    const all = Object.entries(this.index);
    return [
      '[기본 스킬 - 확실히 동작, 먼저 쓸 것]',
      ...all.filter(([, s]) => s.builtin).map(line),
      '[직접 배운 스킬]',
      ...(all.some(([, s]) => !s.builtin) ? all.filter(([, s]) => !s.builtin).map(line) : ['- 아직 없음']),
    ].join('\n');
  }
}
