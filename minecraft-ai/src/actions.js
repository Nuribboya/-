import pf from 'mineflayer-pathfinder';
import { Vec3 } from 'vec3';
import { inventoryOf } from './observe.js';

const { goals } = pf;
const TIER = ['netherite', 'diamond', 'iron', 'stone', 'golden', 'wooden'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * The only surface generated skill code can touch. Every method checks ctx.aborted
 * so a timed-out skill stops acting instead of running on in the background.
 */
export function createApi(bot, ctx, log = () => {}) {
  const reg = bot.registry;
  const check = () => {
    if (ctx.aborted) throw new Error('skill timed out / aborted');
  };

  const matcherFor = (name) => (name === 'log' ? (b) => b.name.endsWith('_log') : (b) => b.name === name);

  const goto = async (x, y, z, range = 1) => {
    check();
    await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range));
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

  const api = {
    log,
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
        if (!table) throw new Error(`cannot craft ${name} now: missing ingredients, or needs a crafting_table nearby (place one first)`);
        await goto(table.position.x, table.position.y, table.position.z, 3);
        recipe = bot.recipesFor(item.id, null, 1, table)[0];
        if (!recipe) throw new Error(`missing ingredients for ${name}`);
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

    /** Wander to find new terrain: walk ~distance blocks in a random direction. */
    async explore(distance = 30) {
      const a = Math.random() * Math.PI * 2;
      const p = bot.entity.position;
      await goto(p.x + Math.cos(a) * distance, p.y, p.z + Math.sin(a) * distance, 3);
    },
  };
  // Safety net: a model-written call that is not awaited must never become an
  // unhandled rejection (that would crash the whole process).
  for (const [k, fn] of Object.entries(api)) {
    if (typeof fn !== 'function') continue;
    api[k] = (...a) => {
      const r = fn(...a);
      if (r && typeof r.catch === 'function') r.catch(() => {});
      return r;
    };
  }
  return api;
}
