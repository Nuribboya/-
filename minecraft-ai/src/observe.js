import { isNight } from './util.js';
const NOTABLE_NAMES = new Set(['stone', 'cobblestone', 'water', 'lava', 'crafting_table', 'furnace', 'chest', 'sand', 'gravel']);
const NOTABLE = (n) => n.endsWith('_log') || n.endsWith('_ore') || n.endsWith('_bed') || NOTABLE_NAMES.has(n);

export const HOSTILE = new Set(['zombie', 'skeleton', 'creeper', 'spider', 'enderman', 'witch', 'drowned', 'husk', 'pillager', 'phantom']);

export function inventoryOf(bot) {
  const inv = {};
  for (const it of bot.inventory.items()) inv[it.name] = (inv[it.name] ?? 0) + it.count;
  return inv;
}

/** Durability of tools/armor in the inventory, with a warning when one is about to break. */
function toolsText(bot) {
  const parts = [];
  for (const it of bot.inventory.items()) {
    if (!it.maxDurability || !/_(pickaxe|axe|shovel|sword|hoe|helmet|chestplate|leggings|boots)$|^shield$/.test(it.name)) continue;
    const left = Math.max(0, it.maxDurability - (it.durabilityUsed ?? 0));
    const pct = Math.round((left / it.maxDurability) * 100);
    parts.push(`${it.name} ${pct}%${pct <= 15 ? '(곧 부서짐! 새로 만들 재료 준비)' : ''}`);
  }
  return parts.length ? `도구 내구도: ${parts.join(', ')}` : '도구 내구도: 도구 없음';
}

/** Text snapshot of the world for the model. */
export function observe(bot) {
  const p = bot.entity.position;
  const inv = inventoryOf(bot);

  const near = {};
  for (const pos of bot.findBlocks({ matching: (b) => NOTABLE(b.name), maxDistance: 24, count: 300 })) {
    const b = bot.blockAt(pos);
    if (!b) continue;
    const d = Math.round(pos.distanceTo(p));
    const cur = near[b.name];
    near[b.name] = { n: (cur?.n ?? 0) + 1, nearest: Math.min(cur?.nearest ?? 99, d) };
  }

  const mobs = Object.values(bot.entities)
    .filter((e) => e !== bot.entity && e.position.distanceTo(p) < 20 && e.name)
    .map((e) => ({ name: e.name, dist: Math.round(e.position.distanceTo(p)), hostile: HOSTILE.has(e.name) }))
    .sort((a, b) => a.dist - b.dist)
    .slice(0, 8);

  return {
    inv,
    text: [
      `위치: (${p.x.toFixed(0)}, ${p.y.toFixed(0)}, ${p.z.toFixed(0)}) / 체력 ${Math.round(bot.health)}/20 / 허기 ${Math.round(bot.food)}/20`,
      `시간: ${isNight(bot) ? '밤 (지표에 적대 몹 많음 - 지하 작업 추천)' : '낮'}`,
      `인벤토리: ${Object.keys(inv).length ? JSON.stringify(inv) : '비어있음'}`,
      toolsText(bot),
      `주변 블록(24칸, 개수/최단거리): ${Object.keys(near).length ? JSON.stringify(near) : '특별한 것 없음'}`,
      `주변 생물: ${mobs.length ? mobs.map((m) => `${m.name}${m.hostile ? '(적대)' : ''}@${m.dist}`).join(', ') : '없음'}`,
    ].join('\n'),
  };
}

/** Minimal state used to judge whether a skill actually did something. */
export function snapshotState(bot) {
  const p = bot.entity.position;
  return {
    inv: inventoryOf(bot),
    pos: p.clone(),
    food: bot.food ?? 0,
    hostiles: Object.values(bot.entities).filter((e) => HOSTILE.has(e.name) && e.position.distanceTo(p) < 16).length,
  };
}

/** Outcome of a skill: inventory delta, distance moved, food gained, hostiles removed. */
export function diffState(a, b) {
  const inv = {};
  for (const k of new Set([...Object.keys(a.inv), ...Object.keys(b.inv)])) {
    const d = (b.inv[k] ?? 0) - (a.inv[k] ?? 0);
    if (d) inv[k] = d;
  }
  const moved = Math.round(a.pos.distanceTo(b.pos));
  const food = b.food - a.food;
  const killed = a.hostiles - b.hostiles;
  const parts = Object.entries(inv).map(([k, v]) => `${k} ${v > 0 ? '+' : ''}${v}`);
  if (moved >= 3) parts.push(`이동 ${moved}칸`);
  if (food > 0) parts.push(`허기 +${food}`);
  if (killed > 0) parts.push(`주변 적대몹 -${killed}`);
  return {
    inv,
    moved,
    effect: Object.keys(inv).length > 0 || moved >= 3 || food > 0 || killed > 0,
    text: parts.length ? parts.join(', ') : '변화 없음',
  };
}
