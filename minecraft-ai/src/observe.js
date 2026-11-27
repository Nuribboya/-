const NOTABLE_NAMES = new Set(['stone', 'cobblestone', 'water', 'lava', 'crafting_table', 'furnace', 'chest', 'sand', 'gravel']);
const NOTABLE = (n) => n.endsWith('_log') || n.endsWith('_ore') || NOTABLE_NAMES.has(n);

const HOSTILE = new Set(['zombie', 'skeleton', 'creeper', 'spider', 'enderman', 'witch', 'drowned', 'husk', 'pillager', 'phantom']);

export function inventoryOf(bot) {
  const inv = {};
  for (const it of bot.inventory.items()) inv[it.name] = (inv[it.name] ?? 0) + it.count;
  return inv;
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

  const tod = bot.time?.timeOfDay ?? 0;
  return {
    inv,
    text: [
      `위치: (${p.x.toFixed(0)}, ${p.y.toFixed(0)}, ${p.z.toFixed(0)}) / 체력 ${Math.round(bot.health)}/20 / 허기 ${Math.round(bot.food)}/20`,
      `시간: ${tod >= 13000 && tod < 23000 ? '밤(위험)' : '낮'}`,
      `인벤토리: ${Object.keys(inv).length ? JSON.stringify(inv) : '비어있음'}`,
      `주변 블록(24칸, 개수/최단거리): ${Object.keys(near).length ? JSON.stringify(near) : '특별한 것 없음'}`,
      `주변 생물: ${mobs.length ? mobs.map((m) => `${m.name}${m.hostile ? '(적대)' : ''}@${m.dist}`).join(', ') : '없음'}`,
    ].join('\n'),
  };
}
