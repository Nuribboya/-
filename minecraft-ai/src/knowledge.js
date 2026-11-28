// Minecraft knowledge computed from game data (minecraft-data via prismarine-*):
// recipes, which tool mines what, smelting, and a step-by-step plan to obtain an item.
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const recipeLibs = new WeakMap();
export function recipesOf(reg) {
  if (!recipeLibs.has(reg)) recipeLibs.set(reg, require('prismarine-recipe')(reg).Recipe);
  return recipeLibs.get(reg);
}

// Furnace outputs -> input (minecraft-data has no smelting recipes).
export const SMELT = {
  iron_ingot: 'raw_iron',
  gold_ingot: 'raw_gold',
  copper_ingot: 'raw_copper',
  stone: 'cobblestone',
  smooth_stone: 'stone',
  glass: 'sand',
  charcoal: 'oak_log',
  cooked_beef: 'beef',
  cooked_porkchop: 'porkchop',
  cooked_chicken: 'chicken',
  cooked_mutton: 'mutton',
};

// Raw resources: always mine these (some also have "unpack a block" recipes we must not use).
const MINE = {
  cobblestone: 'stone',
  coal: 'coal_ore',
  raw_iron: 'iron_ore',
  raw_copper: 'copper_ore',
  raw_gold: 'gold_ore',
  diamond: 'diamond_ore',
  redstone: 'redstone_ore',
  lapis_lazuli: 'lapis_ore',
  emerald: 'emerald_ore',
  dirt: 'dirt',
  sand: 'sand',
  gravel: 'gravel',
  clay_ball: 'clay',
  obsidian: 'obsidian', // needs a diamond pickaxe; get_obsidian also knows how to make it
};

// Items that only drop sometimes from a block: mine that block until enough of the item came out.
export const CHANCE_DROP = { flint: 'gravel' };

// Items you get by killing a mob (white wool: sheep are mostly white).
export const MOB_DROP = {
  white_wool: 'sheep',
  mutton: 'sheep',
  beef: 'cow',
  leather: 'cow',
  porkchop: 'pig',
  chicken: 'chicken',
  feather: 'chicken',
  string: 'spider',
  bone: 'skeleton',
  rotten_flesh: 'zombie',
};

// When the plan says "chop wood", chop at least this much.
export const MIN_WOOD = 10;

// Items smelted per one fuel item.
export const FUEL_VALUE = { coal: 8, charcoal: 8, coal_block: 80, stick: 0.5 };
export const fuelValue = (name) =>
  FUEL_VALUE[name] ?? (name.endsWith('_planks') || name.endsWith('_log') ? 1.5 : 0);

export const TIER_LEVEL = { wooden: 0, golden: 0, stone: 1, copper: 1, iron: 2, diamond: 3, netherite: 4 };
const tierOf = (toolName) => TIER_LEVEL[toolName.split('_')[0]];
const kindOf = (toolName) => toolName.split('_').slice(1).join('_');

/** Weakest tool that can harvest this block (null = hand is fine). */
export function minTool(reg, blockName) {
  const b = reg.blocksByName[blockName];
  if (!b?.harvestTools) return null;
  const names = Object.keys(b.harvestTools)
    .map((id) => reg.items[id]?.name)
    .filter((n) => n && tierOf(n) !== undefined);
  // Same level: prefer the tool that is easiest to make (stone needs no smelting, copper does).
  const EASE = ['wooden', 'stone', 'iron', 'diamond', 'netherite', 'golden', 'copper'];
  const ease = (n) => EASE.indexOf(n.split('_')[0]);
  names.sort((a, b) => tierOf(a) - tierOf(b) || ease(a) - ease(b));
  return names[0] ?? null;
}

/** Does the inventory hold this tool or a stronger one of the same kind? */
export function hasToolAtLeast(inv, toolName) {
  const kind = kindOf(toolName);
  const need = tierOf(toolName);
  return Object.keys(inv).some((k) => (inv[k] ?? 0) > 0 && kindOf(k) === kind && (tierOf(k) ?? -1) >= need);
}

// Can we get this item at all (mine / smelt / craft)? Used to avoid recipe variants
// that need exotic ingredients (blackstone, cobbled_deepslate...).
function obtainable(reg, name) {
  if (MINE[name] || MOB_DROP[name] || name.endsWith('_log') || SMELT[name]) return true;
  const item = reg.itemsByName[name];
  return !!item && recipesOf(reg).find(item.id).length > 0;
}

const family = (n) => n.split('_').slice(1).join('_') || n; // white_bed -> bed

/**
 * Pick the recipe variant we are closest to (counting logs as 4 planks), oak on ties.
 * Skips "recolor" recipes (red_bed + dye -> white_bed) and anything that needs an item we
 * are already trying to make - those send the planner in circles.
 */
function bestVariant(reg, recipes, inv, target = null, stack = new Set()) {
  let best = null;
  let bestScore = Infinity;
  for (const r of recipes) {
    const ins = r.delta.filter((d) => d.count < 0).map((d) => reg.items[d.id]?.name);
    if (ins.some((n) => !n || stack.has(n) || (target && n !== target && family(n) === family(target) && family(target) !== target))) continue;
    let score = 0;
    for (const d of r.delta) {
      if (d.count >= 0) continue;
      const n = reg.items[d.id]?.name;
      let have = inv[n] ?? 0;
      if (n?.endsWith('_planks')) have += 4 * (inv[n.replace('_planks', '_log')] ?? 0);
      score += Math.max(0, -d.count - have) * 10;
      if (n && have === 0 && !obtainable(reg, n)) score += 100; // e.g. cobbled_deepslate
      if (n && !n.startsWith('oak') && have === 0) score += 1; // tie -> oak
    }
    if (score < bestScore) {
      best = r;
      bestScore = score;
    }
  }
  return best;
}

export function recipeInfo(reg, name, inv = {}, stack = new Set()) {
  const item = reg.itemsByName[name];
  if (!item) return null;
  const r = bestVariant(reg, recipesOf(reg).find(item.id), inv, name, stack);
  if (!r) return null;
  const ingredients = {};
  let makes = 1;
  for (const d of r.delta) {
    const n = reg.items[d.id]?.name;
    if (d.count < 0) ingredients[n] = (ingredients[n] ?? 0) - d.count;
    else if (n === name) makes = d.count;
  }
  return { ingredients, makes, requiresTable: !!r.requiresTable };
}

/**
 * Step-by-step plan to have `count` x `target`, starting from `inv`.
 * Steps: {do:'gather', block, item, count} | {do:'craft', item, times, table}
 *        {do:'smelt', input, item, count, fuel} | {do:'place', block} | {do:'unknown', item, count}
 */
export function plan(reg, inv0, target, count = 1, { tableNearby = false, furnaceNearby = false } = {}) {
  const inv = { ...inv0 };
  const steps = [];
  let tableReady = tableNearby;
  let furnaceReady = furnaceNearby;
  const take = (n, c) => (inv[n] = (inv[n] ?? 0) - c);
  const add = (n, c) => (inv[n] = (inv[n] ?? 0) + c);

  const ensurePlaced = (block, depth) => {
    if ((inv[block] ?? 0) < 1) obtain(block, 1, depth + 1);
    else take(block, 1);
    steps.push({ do: 'place', block });
  };

  const stack = new Set();
  function obtain(name, n, depth) {
    stack.add(name);
    try {
      obtainInner(name, n, depth);
    } finally {
      stack.delete(name);
    }
  }

  function obtainInner(name, n, depth) {
    const have = Math.max(0, inv[name] ?? 0);
    if (have >= n) return take(name, n);
    const missing = n - have;
    if (have) take(name, have);
    if (depth > 25) return steps.push({ do: 'unknown', item: name, count: missing });

    if (CHANCE_DROP[name]) {
      const block = CHANCE_DROP[name];
      return steps.push({ do: 'gather', block, item: name, count: missing, until: name, tool: minTool(reg, block) });
    }

    const mineFrom = MINE[name] ?? (name.endsWith('_log') ? name : null);
    if (mineFrom) {
      const tool = minTool(reg, mineFrom);
      if (tool && !hasToolAtLeast(inv, tool)) {
        obtain(tool, 1, depth + 1);
        add(tool, 1); // tools are not used up
      }
      return steps.push({ do: 'gather', block: mineFrom, item: name, count: missing, tool });
    }

    if (MOB_DROP[name]) return steps.push({ do: 'hunt', mob: MOB_DROP[name], item: name, count: missing });

    if (SMELT[name]) {
      const input = SMELT[name];
      obtain(input, missing, depth + 1);
      const fuel = ['coal', 'charcoal'].find((f) => (inv[f] ?? 0) > 0) ?? 'oak_planks';
      obtain(fuel, Math.ceil(missing / fuelValue(fuel)), depth + 1);
      if (!furnaceReady) {
        ensurePlaced('furnace', depth);
        furnaceReady = true;
      }
      return steps.push({ do: 'smelt', input, item: name, count: missing, fuel });
    }

    const info = recipeInfo(reg, name, inv, stack);
    if (info) {
      const times = Math.ceil(missing / info.makes);
      for (const [ing, c] of Object.entries(info.ingredients)) obtain(ing, c * times, depth + 1);
      if (info.requiresTable && !tableReady) {
        ensurePlaced('crafting_table', depth);
        tableReady = true;
      }
      steps.push({ do: 'craft', item: name, times, table: info.requiresTable });
      return add(name, times * info.makes - missing); // leftovers
    }
    steps.push({ do: 'unknown', item: name, count: missing });
  }

  obtain(target, count, 0);

  // Gathering earlier never hurts: merge repeated gathers of the same block into the first one,
  // and merge back-to-back crafts of the same item.
  const merged = [];
  for (const s of steps) {
    const first = s.do === 'gather' && merged.find((m) => m.do === 'gather' && m.block === s.block && m.until === s.until);
    const prev = merged.at(-1);
    if (first) first.count += s.count;
    else if (s.do === 'craft' && prev?.do === 'craft' && prev.item === s.item) prev.times += s.times;
    else merged.push({ ...s });
  }
  // Wood is needed again and again (tables, sticks, fuel): when chopping anyway, take a stack.
  for (const s of merged) if (s.do === 'gather' && s.block.endsWith('_log')) s.count = Math.max(s.count, MIN_WOOD);
  return merged;
}

/** Map a plan step to a builtin skill call the decision model can use directly. */
export function stepToAction(s) {
  switch (s.do) {
    case 'gather':
      if (s.block.endsWith('_log')) return { skill: 'collect_logs', args: { count: s.count } };
      if (s.item === 'obsidian') return { skill: 'get_obsidian', args: { count: s.count } };
      if (s.until) return { skill: 'mine_block', args: { block: s.block, count: s.count, until: s.until } };
      return { skill: 'mine_block', args: { block: s.block, count: s.count } };
    case 'craft':
      return { skill: 'craft_item', args: { item: s.item, count: s.times } };
    case 'smelt':
      return { skill: 'smelt_item', args: { item: s.input, count: s.count, fuel: s.fuel } };
    case 'hunt':
      return { skill: 'hunt_animal', args: { mob: s.mob, count: s.count } };
    case 'place':
      return { skill: 'place_block', args: { block: s.block } };
    default:
      return null;
  }
}

export function stepText(s) {
  switch (s.do) {
    case 'gather':
      if (s.until) return `${s.block} 캐기 (${s.item} x${s.count} 나올 때까지)`;
      if (s.item === 'obsidian') return `흑요석 x${s.count} 얻기 (다이아 곡괭이로 캐거나, 용암에 물을 부어 만들기)`;
      return `${s.block} 캐기 x${s.count}${s.tool ? ` (${s.tool} 이상 필요)` : ''}`;
    case 'craft':
      return `${s.item} 제작 x${s.times}회${s.table ? ' (작업대 옆에서)' : ''}`;
    case 'smelt':
      return `화로에서 ${s.input} x${s.count} 굽기 → ${s.item} (연료 ${s.fuel})`;
    case 'place':
      return `${s.block} 설치`;
    case 'hunt':
      return `${s.mob} 잡기 x${s.count} → ${s.item}`;
    default:
      return `${s.item} x${s.count} 얻는 방법 모름 (탐색 필요)`;
  }
}

/** Compact facts for the system prompt, generated from game data. */
export function knowledgeText(reg) {
  const tool = (b) => minTool(reg, b) ?? '맨손';
  return [
    '게임 데이터 기반 지식:',
    `- 채굴 도구: 원목/흙/모래=맨손, stone→cobblestone=${tool('stone')}, coal_ore=${tool('coal_ore')}, iron_ore=${tool('iron_ore')}, diamond_ore=${tool('diamond_ore')} (이상 등급이면 됨, 등급: wooden<stone=copper<iron<diamond)`,
    '- 제작: 원목1→판자4, 판자2→막대기4, 작업대=판자4, 곡괭이=재료3+막대기2(작업대 필요), 화로=조약돌8(작업대 필요)',
    '- 굽기(화로): raw_iron→iron_ingot, sand→glass, 연료 coal 1개=8개 굽기, 판자 1개=1.5개',
    '- 침대=같은 색 양털3+판자3(작업대). 양털은 양을 잡으면 1개. 밤에만 잘 수 있고 근처에 몬스터가 있으면 못 잔다. 자면 부활 지점이 침대로 바뀐다.',
    `- 광석 깊이: ${Object.entries(ORE_DEPTH).map(([k, v]) => `${k} y≈${v}`).join(', ')}. 깊이 내려갈 땐 descend_mine, 동굴은 explore_cave. 용암 주의.`,
  ].join('\n');
}

// Where ores are most common (1.18+ generation). Used for "go deeper" hints.
export const ORE_DEPTH = {
  diamond_ore: -58,
  redstone_ore: -58,
  gold_ore: -16,
  lapis_ore: 0,
  iron_ore: 16,
  copper_ore: 48,
  coal_ore: 96,
};

/** Hint after failing to find `name` nearby while standing at height y. */
export function oreHint(name, y) {
  const d = ORE_DEPTH[name];
  if (d !== undefined && Math.abs(y - d) > 20)
    return `${name} is most common around y=${d} and you are at y=${y}: ${y > d ? `use descend_mine {y: ${d}} (or explore_cave) first` : 'go up / explore_area'}`;
  return 'try explore_cave or explore_area to look somewhere else';
}

/** Block to mine for an item the model asked for by its drop name (raw_iron -> iron_ore). */
export function blockForItem(name) {
  return MINE[name] ?? null;
}

/** Closest real item names for a misspelled/made-up one (e.g. iron_chest_plate -> iron_chestplate). */
export function similarItems(reg, name, n = 3) {
  return similarNames(Object.keys(reg.itemsByName), name, n);
}

export function similarNames(names, name, n = 3) {
  const want = String(name).toLowerCase();
  const words = new Set(want.split('_'));
  const dist = (a, b) => {
    const d = Array.from({ length: a.length + 1 }, (_, i) => [i]);
    for (let j = 1; j <= b.length; j++) d[0][j] = j;
    for (let i = 1; i <= a.length; i++)
      for (let j = 1; j <= b.length; j++)
        d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    return d[a.length][b.length];
  };
  return names
    .map((k) => ({ k, score: k.split('_').filter((w) => words.has(w)).length * 10 - dist(want, k) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, n)
    .map((x) => x.k);
}
