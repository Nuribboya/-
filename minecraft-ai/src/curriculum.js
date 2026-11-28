// Automatic curriculum: the first unmet milestone is the current goal.
// Milestones, once achieved, stay achieved (items get consumed by crafting).

const count = (inv, re) =>
  Object.entries(inv).reduce((n, [k, v]) => (re.test(k) ? n + v : n), 0);
const has = (inv, re) => count(inv, re) > 0;

const MILESTONES = [
  { id: 'logs', target: { item: 'log', count: 4 }, goal: '나무 원목(log) 4개 이상 모으기', done: (i) => count(i, /_log$/) >= 4 || has(i, /^(crafting_table|wooden_pickaxe|stone_pickaxe|iron_pickaxe)$/) },
  { id: 'table', target: { item: 'crafting_table', count: 1 }, goal: '작업대(crafting_table) 만들기', done: (i) => has(i, /^(crafting_table|wooden_pickaxe|stone_pickaxe|iron_pickaxe)$/) },
  { id: 'wooden_pickaxe', target: { item: 'wooden_pickaxe', count: 1 }, goal: '나무 곡괭이(wooden_pickaxe) 만들기', done: (i) => has(i, /_pickaxe$/) },
  { id: 'stone_pickaxe', target: { item: 'stone_pickaxe', count: 1 }, goal: '돌(cobblestone)을 캐서 돌 곡괭이(stone_pickaxe) 만들기', done: (i) => has(i, /^(stone|iron|diamond)_pickaxe$/) },
  { id: 'furnace', target: { item: 'furnace', count: 1 }, goal: '화로(furnace) 만들기 (cobblestone 8개)', done: (i) => has(i, /^(furnace|iron_ingot|iron_pickaxe|diamond_pickaxe)$/) },
  { id: 'iron_ingot', target: { item: 'iron_ingot', count: 3 }, goal: '철광석(raw_iron)을 캐고 화로에서 구워 철 주괴(iron_ingot) 3개 만들기', done: (i) => count(i, /^iron_ingot$/) >= 3 || has(i, /^(iron|diamond)_pickaxe$/) },
  { id: 'iron_pickaxe', target: { item: 'iron_pickaxe', count: 1 }, goal: '철 곡괭이(iron_pickaxe) 만들기', done: (i) => has(i, /^(iron|diamond)_pickaxe$/) },
];

const FINAL_GOAL = '기본 도구 완성 - 이제 스스로 목표를 정해서 플레이';

export function nextGoal(inv, memory) {
  for (const m of MILESTONES) {
    if (memory.data.achieved.includes(m.id)) continue;
    if (m.done(inv)) {
      memory.achieve(m.id);
      continue;
    }
    return { id: m.id, text: m.goal, target: m.target };
  }
  return { id: 'free', text: FINAL_GOAL };
}
