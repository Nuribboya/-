import pf from 'mineflayer-pathfinder';
import { Vec3 } from 'vec3';
import { inventoryOf, HOSTILE } from './observe.js';

const { goals } = pf;
const TIER = ['netherite', 'diamond', 'iron', 'stone', 'golden', 'wooden'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NEIGHBORS = [[0, -1, 0], [0, 1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]];
const EQUIP_SLOTS = { hand: 'hand', 'off-hand': 'off-hand', offhand: 'off-hand', head: 'head', torso: 'torso', chest: 'torso', legs: 'legs', feet: 'feet' };
const OFF_HAND_SLOT = 45;
const isOpen = (b) => !b || b.boundingBox === 'empty'; // air, grass, water, flowers...

/**
 * The only surface generated skill code can touch. Every method checks ctx.aborted
 * so a timed-out skill stops acting instead of running on in the background.
 */
export function createApi(bot, ctx, log = () => {}, hooks = {}) {
  const reg = bot.registry;
  const check = () => {
    if (ctx.aborted) throw new Error('skill timed out / aborted');
  };

  const matcherFor = (name) => (name === 'log' ? (b) => b.name.endsWith('_log') : (b) => b.name === name);

  const goto = async (x, y, z, range = 1) => {
    check();
    try {
      await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range));
    } catch (e) {
      // A leftover stop/goal change from earlier code can cancel a fresh move once; retry one time.
      if (ctx.aborted || !['PathStopped', 'GoalChanged'].includes(e?.name)) throw e;
      check();
      await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range));
    }
    check();
  };

  const bestTool = (kind) => {
    const items = bot.inventory.items().filter((i) => i.name.endsWith(`_${kind}`));
    items.sort((a, b) => TIER.indexOf(a.name.split('_')[0]) - TIER.indexOf(b.name.split('_')[0]));
    return items[0];
  };

  const equipFor = async (block) => {
    const kind = block.material?.includes('/') ? block.material.split('/')[1] : null;
    const tool = kind && bestTool(kind);
    if (tool) await bot.equip(tool, 'hand');
    if (block.harvestTools && !bot.inventory.items().some((i) => block.harvestTools[i.type])) {
      const need = Object.keys(block.harvestTools).map((id) => reg.items[id]?.name).filter(Boolean);
      throw new Error(`need a better tool to mine ${block.name} (one of: ${need.join(', ')})`);
    }
  };

  /** Human/model-readable explanation of why `name` can't be crafted right now. */
  const explainCraft = (name, item, tableNearby) => {
    const all = bot.recipesAll(item.id, null, true);
    if (!all.length) return `${name} has no crafting recipe - it must be mined, smelted or found`;
    const inv = inventoryOf(bot);
    const needOf = (r) => {
      const need = {};
      for (const d of r.delta) if (d.count < 0) {
        const n = reg.items[d.id]?.name ?? String(d.id);
        need[n] = (need[n] ?? 0) - d.count;
      }
      return need;
    };
    // Pick the variant closest to craftable; among ties prefer the wood type we hold logs of.
    let best = null;
    let bestScore = Infinity;
    for (const r of all) {
      const need = needOf(r);
      let score = 0;
      for (const [n, c] of Object.entries(need)) {
        score += Math.max(0, c - (inv[n] ?? 0)) * 10;
        if (n.endsWith('_planks') && (inv[n.replace('_planks', '_log')] ?? 0) > 0) score -= 1;
      }
      if (score < bestScore) {
        best = { r, need };
        bestScore = score;
      }
    }
    const parts = Object.entries(best.need).map(([n, c]) => `${n} x${c} (have ${inv[n] ?? 0})`);
    const hints = [];
    for (const [n, c] of Object.entries(best.need)) {
      if ((inv[n] ?? 0) >= c) continue;
      if (n.endsWith('_planks')) hints.push(`await api.craft('${n}') turns 1 ${n.replace('_planks', '_log')} into 4 ${n}`);
      else if (n === 'stick') hints.push("await api.craft('stick') turns 2 planks into 4 sticks");
    }
    const tableNote = best.r.requiresTable && !tableNearby ? ' Also needs a crafting_table PLACED within 32 blocks (await api.place("crafting_table")).' : '';
    return `cannot craft ${name}: needs ${parts.join(', ')}.${tableNote}${hints.length ? ' Next step: ' + hints.join('; ') : ''}`;
  };

  const api = {
    log,
    /** Run another saved skill by name from inside a skill: await api.useSkill('collect_logs', {count: 3}) */
    useSkill: async (name, args = {}) => {
      check();
      if (!hooks.useSkill) throw new Error('useSkill is not available here');
      return hooks.useSkill(name, args);
    },
    sleep: async (ms) => {
      check();
      await sleep(Math.min(ms, 10000));
      check();
    },
    chat: (msg) => bot.chat(String(msg).slice(0, 200)),
    position: () => bot.entity.position.floored(),
    health: () => bot.health,
    food: () => bot.food,
    inventory: () => inventoryOf(bot),
    has: (name, n = 1) => (inventoryOf(bot)[name] ?? 0) >= n,

    /** Count of matching blocks within maxDistance. name 'log' matches any log type. */
    countBlocks: (name, maxDistance = 32) =>
      bot.findBlocks({ matching: matcherFor(name), maxDistance, count: 500 }).length,

    goTo: goto,

    /** Mine `count` blocks. name 'log' = any log. Walks to them, equips best tool, picks up drops. */
    async gather(name, count = 1, maxDistance = 48) {
      let got = 0;
      while (got < count) {
        check();
        const [pos] = bot.findBlocks({ matching: matcherFor(name), maxDistance, count: 1 });
        if (!pos) throw new Error(`no ${name} within ${maxDistance} blocks (got ${got}/${count}) - explore somewhere else`);
        await goto(pos.x, pos.y, pos.z, 2);
        const block = bot.blockAt(pos);
        if (!block || !matcherFor(name)(block)) continue;
        await equipFor(block);
        await bot.dig(block);
        got++;
        await goto(pos.x, pos.y, pos.z, 0).catch(() => {});
        await sleep(250);
      }
      return got;
    },

    /** Craft `times` times. Uses a nearby crafting table automatically when the recipe needs one. */
    async craft(name, times = 1) {
      check();
      const item = reg.itemsByName[name];
      if (!item) throw new Error(`unknown item: ${name}`);
      let table = null;
      let recipe = bot.recipesFor(item.id, null, 1, null)[0];
      if (!recipe) {
        const tableId = reg.blocksByName.crafting_table.id;
        table = bot.findBlock({ matching: tableId, maxDistance: 32 });
        if (!table) throw new Error(explainCraft(name, item, false));
        recipe = bot.recipesFor(item.id, null, 1, table)[0];
        if (!recipe) throw new Error(explainCraft(name, item, true));
        await goto(table.position.x, table.position.y, table.position.z, 3);
      }
      await bot.craft(recipe, times, table);
      return inventoryOf(bot)[name] ?? 0;
    },

    /** Place one block from inventory on the ground next to you. */
    async place(name) {
      check();
      const item = bot.inventory.items().find((i) => i.name === name);
      if (!item) throw new Error(`no ${name} in inventory`);
      await bot.equip(item, 'hand');
      const base = bot.entity.position.floored();
      for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1]]) {
        const ground = bot.blockAt(base.offset(dx, -1, dz));
        const target = bot.blockAt(base.offset(dx, 0, dz));
        if (ground && ground.boundingBox === 'block' && target && target.name === 'air') {
          await bot.placeBlock(ground, new Vec3(0, 1, 0));
          return base.offset(dx, 0, dz);
        }
      }
      throw new Error('no free spot to place the block here - move first');
    },

    /** Smelt `count` of `input` (e.g. raw_iron) in a nearby furnace using `fuel`. */
    async smelt(input, count = 1, fuel = 'coal') {
      check();
      const furnaceBlock = bot.findBlock({ matching: reg.blocksByName.furnace.id, maxDistance: 32 });
      if (!furnaceBlock) throw new Error('no furnace nearby - craft and place one');
      const inItem = bot.inventory.items().find((i) => i.name === input);
      const fuelItem = bot.inventory.items().find((i) => i.name === fuel);
      if (!inItem || inItem.count < count) throw new Error(`need ${count} ${input}`);
      if (!fuelItem) throw new Error(`no fuel (${fuel}) in inventory`);
      await goto(furnaceBlock.position.x, furnaceBlock.position.y, furnaceBlock.position.z, 2);
      const furnace = await bot.openFurnace(furnaceBlock);
      try {
        await furnace.putFuel(fuelItem.type, null, Math.min(fuelItem.count, Math.max(1, Math.ceil(count / 8))));
        await furnace.putInput(inItem.type, null, count);
        let collected = 0;
        for (let i = 0; i < count * 12 && collected < count; i++) {
          check();
          await sleep(1000);
          if (furnace.outputItem()) {
            const out = await furnace.takeOutput();
            collected += out?.count ?? 0;
          }
        }
        return collected;
      } finally {
        furnace.close();
      }
    },

    async eat() {
      check();
      const foods = bot.inventory.items().filter((i) => reg.foodsByName?.[i.name]);
      if (!foods.length) throw new Error('no food in inventory');
      foods.sort((a, b) => reg.foodsByName[b.name].foodPoints - reg.foodsByName[a.name].foodPoints);
      await bot.equip(foods[0], 'hand');
      await bot.consume();
    },

    /** Fight the nearest entity with this name (e.g. 'zombie') until it dies or ~20s pass. */
    async attack(name = 'zombie') {
      check();
      const sword = bestTool('sword') ?? bestTool('axe');
      if (sword) await bot.equip(sword, 'hand');
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline) {
        check();
        const target = bot.nearestEntity((e) => e.name === name);
        if (!target) return true;
        if (target.position.distanceTo(bot.entity.position) > 3) {
          await goto(target.position.x, target.position.y, target.position.z, 2).catch(() => {});
        }
        await bot.lookAt(target.position.offset(0, target.height * 0.8, 0));
        bot.attack(target);
        await sleep(600);
      }
      return false;
    },

    /** Nearby living entities with coordinates, nearest first: [{name, x, y, z, dist, hostile}] */
    nearbyMobs(maxDistance = 16) {
      const me = bot.entity.position;
      return Object.values(bot.entities)
        .filter((e) => e !== bot.entity && e.name && e.type !== 'object' && e.type !== 'orb' && e.position.distanceTo(me) <= maxDistance)
        .map((e) => ({
          name: e.name,
          x: Math.floor(e.position.x),
          y: Math.floor(e.position.y),
          z: Math.floor(e.position.z),
          dist: Math.round(e.position.distanceTo(me)),
          hostile: HOSTILE.has(e.name),
        }))
        .sort((a, b) => a.dist - b.dist);
    },

    /** Place a block from inventory at exact coordinates (needs a solid neighbor to attach to). */
    async placeAt(x, y, z, name) {
      check();
      const item = bot.inventory.items().find((i) => i.name === name);
      if (!item) throw new Error(`no ${name} in inventory`);
      const target = new Vec3(Math.floor(x), Math.floor(y), Math.floor(z));
      if (!isOpen(bot.blockAt(target))) throw new Error(`(${target.x},${target.y},${target.z}) is not empty (${bot.blockAt(target).name})`);
      const feet = bot.entity.position.floored();
      if (target.x === feet.x && target.z === feet.z && (target.y === feet.y || target.y === feet.y + 1))
        throw new Error('cannot place a block inside yourself - pick a spot next to you or above your head (y+2)');
      let ref = null;
      let face = null;
      for (const [dx, dy, dz] of NEIGHBORS) {
        const b = bot.blockAt(target.offset(dx, dy, dz));
        if (b && b.boundingBox === 'block') {
          ref = b;
          face = new Vec3(-dx, -dy, -dz);
          break;
        }
      }
      if (!ref) throw new Error(`nothing solid next to (${target.x},${target.y},${target.z}) to attach the block to`);
      if (bot.entity.position.distanceTo(target) > 4) await goto(target.x, target.y, target.z, 3);
      await bot.equip(item, 'hand');
      check();
      await bot.placeBlock(ref, face);
      return target;
    },

    /** Mine the block at exact coordinates (e.g. dig down to hide: api.digAt(x, y-1, z)). */
    async digAt(x, y, z) {
      check();
      const block = bot.blockAt(new Vec3(Math.floor(x), Math.floor(y), Math.floor(z)));
      if (!block || isOpen(block)) throw new Error(`nothing to dig at (${x},${y},${z})`);
      if (block.diggable === false) throw new Error(`${block.name} cannot be dug`);
      if (bot.entity.position.distanceTo(block.position) > 4) await goto(block.position.x, block.position.y, block.position.z, 3);
      await equipFor(block);
      check();
      await bot.dig(block);
      return block.name;
    },

    /** Equip an item. slot: hand | off-hand | head | torso | legs | feet  (armor, shield, sword...) */
    async equip(name, slot = 'hand') {
      check();
      const dest = EQUIP_SLOTS[slot];
      if (!dest) throw new Error(`unknown slot "${slot}" - use one of: hand, off-hand, head, torso, legs, feet`);
      const item = bot.inventory.items().find((i) => i.name === name);
      if (!item) throw new Error(`no ${name} in inventory`);
      await bot.equip(item, dest);
      return dest;
    },

    /** Raise the shield held in the off-hand for ms (max 5s). Equip it first: api.equip('shield', 'off-hand') */
    async block(ms = 1500) {
      check();
      if (bot.inventory.slots[OFF_HAND_SLOT]?.name !== 'shield') throw new Error("no shield in off-hand - first: await api.equip('shield', 'off-hand')");
      bot.activateItem(true);
      try {
        await sleep(Math.min(ms, 5000));
      } finally {
        bot.deactivateItem();
      }
      return true;
    },

    /** Wander to find new terrain: walk ~distance blocks in a random direction. */
    async explore(distance = 30) {
      const a = Math.random() * Math.PI * 2;
      const p = bot.entity.position;
      await goto(p.x + Math.cos(a) * distance, p.y, p.z + Math.sin(a) * distance, 3);
    },
  };
  // Body actions run one at a time: Promise.all([gather a, gather b]) would make two
  // pathfinder goals cancel each other ("Path was stopped"). useSkill is not queued
  // (it calls these itself, queuing it would deadlock).
  const SERIAL = new Set(['gather', 'craft', 'place', 'smelt', 'goTo', 'explore', 'attack', 'eat', 'placeAt', 'digAt', 'equip', 'block']);
  let queue = Promise.resolve();
  for (const k of SERIAL) {
    const fn = api[k];
    api[k] = (...a) => {
      const run = queue.then(() => fn(...a));
      queue = run.catch(() => {});
      return run;
    };
  }

  // Safety net: a model-written call that is not awaited must never become an
  // unhandled rejection (that would crash the whole process).
  // Also record every api error in ctx.errors, so errors the skill code swallows with
  // try/catch are still visible to the model ("nothing changed - because craft failed: ...").
  const note = (k, e) => {
    if (!ctx.errors) ctx.errors = [];
    if (ctx.errors.length < 10) ctx.errors.push(`api.${k}: ${e?.message ?? e}`);
  };
  for (const [k, fn] of Object.entries(api)) {
    if (typeof fn !== 'function') continue;
    api[k] = (...a) => {
      let r;
      try {
        r = fn(...a);
      } catch (e) {
        note(k, e);
        throw e;
      }
      if (r && typeof r.catch === 'function') r.catch((e) => note(k, e));
      return r;
    };
  }
  return api;
}
