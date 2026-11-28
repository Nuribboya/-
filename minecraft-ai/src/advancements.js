// Advancement tracking + the advancements the bot can work toward with its current tools.
// Progress comes from the server's `advancements` packet (sent in full on join, so past
// advancements are known too); the "<name> has made the advancement [Title]" chat line is a backup.

// In order. `target`: item to obtain (the planner gets there). `finish`: what to do once we have it.
export const ADVANCEMENT_GOALS = [
  {
    id: 'minecraft:story/obtain_armor',
    title: 'Suit Up',
    text: '발전 과제 "Suit Up"(무장하자): 철 투구를 만들어 머리에 쓰기',
    target: { item: 'iron_helmet', count: 1 },
    finish: { text: '철 투구 머리에 쓰기', action: { skill: 'equip_item', args: { item: 'iron_helmet', slot: 'head' } } },
  },
  {
    id: 'minecraft:husbandry/root',
    title: 'Husbandry',
    text: '발전 과제 "Husbandry": 음식을 하나 먹기 (구운 고기 추천)',
    target: { item: 'cooked_beef', count: 1 },
    finish: { text: '음식 먹기', action: { skill: 'eat_food', args: {} } },
  },
  {
    id: 'minecraft:adventure/kill_a_mob',
    title: 'Monster Hunter',
    text: '발전 과제 "Monster Hunter"(몬스터 사냥꾼): 적대 몹 하나 처치 (밤에 좀비 추천)',
    target: null,
    finish: { text: '좀비 처치', action: { skill: 'hunt_animal', args: { mob: 'zombie', count: 1 } } },
  },
  {
    id: 'minecraft:story/deflect_arrow',
    title: 'Not Today, Thank You',
    text: '발전 과제 "Not Today, Thank You": 방패를 왼손에 들고 스켈레톤 화살을 막기 (밤에 스켈레톤을 찾아 block_arrows)',
    target: { item: 'shield', count: 1 },
    finish: { text: '방패 왼손에 들기', action: { skill: 'equip_item', args: { item: 'shield', slot: 'off-hand' } } },
    // once it is in the off-hand, the advancement needs an actual blocked arrow
    then: { text: '스켈레톤을 바라보며 방패로 화살 막기', action: { skill: 'block_arrows', args: { seconds: 8 } } },
  },
  {
    id: 'minecraft:adventure/sleep_in_bed',
    title: 'Sweet Dreams',
    text: '발전 과제 "Sweet Dreams": 침대를 만들어 밤에 자기',
    target: { item: 'white_bed', count: 1 },
    finish: { text: '밤에 침대에서 자기', action: { skill: 'sleep_in_bed', args: {} } },
  },
  {
    id: 'minecraft:story/mine_diamond',
    title: 'Diamonds!',
    text: '발전 과제 "Diamonds!": 다이아몬드 캐기 (y≈-58까지 descend_mine)',
    target: { item: 'diamond', count: 1 },
    finish: null,
  },
  {
    id: 'minecraft:story/shiny_gear',
    title: 'Cover Me with Diamonds',
    text: '발전 과제 "Cover Me with Diamonds": 다이아 투구(다이아 5)를 만들어 쓰기',
    target: { item: 'diamond_helmet', count: 1 },
    finish: { text: '다이아 투구 머리에 쓰기', action: { skill: 'equip_item', args: { item: 'diamond_helmet', slot: 'head' } } },
  },
];

const ADV_LINE = /^(\S+) has (?:made the advancement|completed the challenge|reached the goal) \[(.+)\]$/;

export class Advancements {
  constructor(saved = []) {
    this.requirements = new Map(); // id -> [[criterion, ...], ...]  (all groups, any criterion per group)
    this.progress = new Map(); // id -> Set(obtained criteria)
    this.fromChat = new Set(saved); // ids seen in chat (backup when no packet data)
    this.listeners = [];
  }

  onDone(fn) {
    this.listeners.push(fn);
  }

  /** Feed the server's `advancements` packet. */
  onPacket(packet) {
    const before = new Set(this.doneIds());
    if (packet.reset) {
      this.requirements.clear();
      this.progress.clear();
    }
    for (const { key, value } of packet.advancementMapping ?? []) this.requirements.set(key, value?.requirements ?? []);
    for (const id of packet.identifiers ?? []) {
      this.requirements.delete(id);
      this.progress.delete(id);
    }
    for (const { key, value } of packet.progressMapping ?? []) {
      const got = this.progress.get(key) ?? new Set();
      for (const c of Array.isArray(value) ? value : value?.criteria ?? []) {
        if (c.criterionProgress !== undefined && c.criterionProgress !== null) got.add(c.criterionIdentifier);
        else got.delete(c.criterionIdentifier);
      }
      this.progress.set(key, got);
    }
    // Only announce new ones after the first full sync (the join packet lists everything).
    if (this.synced) for (const id of this.doneIds()) if (!before.has(id)) this.#emit(id);
    this.synced = true;
  }

  /** Feed a chat line; returns the advancement id if it was ours. */
  onChat(msg, username) {
    const m = ADV_LINE.exec(msg);
    if (!m || m[1] !== username) return null;
    const entry = ADVANCEMENT_GOALS.find((a) => a.title === m[2]);
    const id = entry?.id ?? `title:${m[2]}`;
    if (!this.isDone(id)) {
      this.fromChat.add(id);
      this.#emit(id);
    }
    return id;
  }

  isDone(id) {
    if (this.fromChat.has(id)) return true;
    const got = this.progress.get(id);
    if (!got || !got.size) return false;
    const req = this.requirements.get(id);
    return !req || !req.length ? true : req.every((group) => group.some((c) => got.has(c)));
  }

  doneIds() {
    return [...new Set([...this.progress.keys(), ...this.fromChat])].filter((id) => this.isDone(id));
  }

  /** Next advancement goal we have not done (skipping ids in `skip`). */
  next(skip = new Set()) {
    return ADVANCEMENT_GOALS.find((a) => !this.isDone(a.id) && !skip.has(a.id)) ?? null;
  }

  #emit(id) {
    const entry = ADVANCEMENT_GOALS.find((a) => a.id === id);
    for (const fn of this.listeners) fn(id, entry?.title ?? id.replace(/^title:/, '').replace(/^minecraft:/, ''));
  }
}
