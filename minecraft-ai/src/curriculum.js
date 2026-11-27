// Automatic curriculum: the first unmet milestone is the current goal.
// Milestones, once achieved, stay achieved (items get consumed by crafting).

const count = (inv, re) =>
  Object.entries(inv).reduce((n, [k, v]) => (re.test(k) ? n + v : n), 0);
const has = (inv, re) => count(inv, re) > 0;

const MILESTONES = [
  { id: 'logs', goal: '나무 원목(log) 4개 이상 모으기', done: (i) => count(i, /_log$/) >= 4 || has(i, /^(crafting_table|wooden_pickaxe|stone_pickaxe|iron_pickaxe)$/) },
  { id: 'table', goal: '작업대(crafting_table) 만들기', done: (i) => has(i, /^(crafting_table|wooden_pickaxe|stone_pickaxe|iron_pickaxe)$/) },
  { id: 'wooden_pickaxe', goal: '나무 곡괭이(wooden_pickaxe) 만들기', done: (i) => has(i, /_pickaxe$/) },
  { id: 'stone_pickaxe', goal: '돌(cobblestone)을 캐서 돌 곡괭이(stone_pickaxe) 만들기', done: (i) => has(i, /^(stone|iron|diamond)_pickaxe$/) },
  { id: 'furnace', goal: '화로(furnace) 만들기 (cobblestone 8개)', done: (i) => has(i, /^(furnace|iron_ingot|iron_pickaxe|diamond_pickaxe)$/) },
  { id: 'iron_ingot', goal: '철광석(raw_iron)을 캐고 화로에서 구워 철 주괴(iron_ingot) 3개 만들기', done: (i) => count(i, /^iron_ingot$/) >= 3 || has(i, /^(iron|diamond)_pickaxe$/) },
  { id: 'iron_pickaxe', goal: '철 곡괭이(iron_pickaxe) 만들기', done: (i) => has(i, /^(iron|diamond)_pickaxe$/) },
  { id: 'diamond', goal: '다이아몬드(diamond) 찾아서 캐기 (낮은 Y 레벨 탐색)', done: (i) => has(i, /^diamond$/) },
];

const FINAL_GOAL = '살아남으면서 자유롭게 탐험하고 자원 모으기 (음식, 침대, 철 도구)';

export function nextGoal(inv, memory) {
  for (const m of MILESTONES) {
    if (memory.data.achieved.includes(m.id)) continue;
    if (m.done(inv)) {
      memory.achieve(m.id);
      continue;
    }
    return { id: m.id, text: m.goal };
  }
  return { id: 'free', text: FINAL_GOAL };
}
