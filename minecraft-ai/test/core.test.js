import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseJson } from '../src/llm.js';
import { runSkill, checkCode } from '../src/sandbox.js';
import { Skills } from '../src/skills.js';
import { Memory } from '../src/memory.js';
import { nextGoal } from '../src/curriculum.js';
import { parseWriterReply } from '../src/prompts.js';

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'mcai-'));

test('parseJson tolerates fences and chatter', () => {
  assert.deepEqual(parseJson('네!\n```json\n{"a":1}\n```'), { a: 1 });
  assert.throws(() => parseJson('no json'));
});

test('sandbox runs code with api/args and returns', async () => {
  const out = await runSkill('return api.add(args.x, 2);', { add: (a, b) => a + b }, { x: 3 }, { aborted: false });
  assert.equal(out, 5);
});

test('sandbox blocks escape identifiers', () => {
  for (const bad of ['process.exit()', 'require("fs")', 'api.constructor.constructor("return process")()', 'eval("1")'])
    assert.throws(() => checkCode(bad), /banned/);
});

test('sandbox times out and aborts', async () => {
  const ctx = { aborted: false };
  await assert.rejects(runSkill('await api.sleep(5000);', { sleep: (ms) => new Promise((r) => setTimeout(r, ms)) }, {}, ctx, 50), /exceeded/);
  assert.equal(ctx.aborted, true);
});

test('skills: seeds exist, save/record/describe', () => {
  const s = new Skills(tmp());
  assert.ok(s.has('collect_logs'));
  s.save('make_torch', '횃불 제작', { count: 4 }, 'return 1;');
  assert.equal(s.code('make_torch'), 'return 1;');
  s.record('make_torch', false);
  s.record('make_torch', false);
  assert.match(s.describe(), /연속실패/);
  assert.throws(() => s.save('Bad Name', '', {}, ''));
});

test('curriculum advances and persists achievements', () => {
  const m = new Memory(tmp());
  assert.equal(nextGoal({}, m).id, 'logs');
  assert.equal(nextGoal({ oak_log: 5 }, m).id, 'table');
  assert.equal(nextGoal({ oak_planks: 2 }, m).id, 'table'); // logs stay achieved after crafting
  assert.equal(nextGoal({ wooden_pickaxe: 1 }, m).id, 'stone_pickaxe');
});

test('parseWriterReply extracts fields', () => {
  const r = parseWriterReply('NAME: make_torch\nDESCRIPTION: 횃불 만들기\nARGS: {"count": 4}\nCODE:\n```js\nreturn 1;\n```');
  assert.equal(r.name, 'make_torch');
  assert.deepEqual(r.args, { count: 4 });
  assert.equal(r.code, 'return 1;');
});

test('ollama adapter sends ctx/json format and reads reply', async () => {
  const { LLM } = await import('../src/llm.js');
  let body;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    body = JSON.parse(init.body);
    return { ok: true, json: async () => ({ message: { content: '{"wait":true}' } }) };
  };
  try {
    const llm = new LLM({ fast: { provider: 'ollama', model: 'm' }, smart: { provider: 'ollama', model: 'm' }, ollamaUrl: 'http://x', ollamaCtx: 8192, dailyBudgetUsd: 1 });
    assert.equal(await llm.ask('fast', 'sys', 'hi'), '{"wait":true}');
    assert.equal(body.format, 'json');
    assert.equal(body.options.num_ctx, 8192);
    assert.equal(body.messages[0].role, 'system');
    await llm.ask('smart', 'sys', 'hi');
    assert.equal(body.format, undefined);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('journal records, summarizes deaths, reloads from disk', async () => {
  const { Journal } = await import('../src/journal.js');
  const dir = tmp();
  const j = new Journal(dir);
  j.add('death', { cause: 'was slain by Zombie', where: '1,64,2', night: true, lastAction: 'collect_logs' });
  j.add('death', { cause: 'was slain by Zombie', where: '3,64,4', night: false, lastAction: 'explore_area' });
  j.add('skill_fail', { name: 'x', error: 'boom' });
  assert.match(j.deathSummary(), /죽음 2회: was slain by Zombie 2 \/ 밤에 1회/);
  assert.match(j.recentText(), /boom/);
  assert.equal(new Journal(dir).entries.length, 3);
  assert.match(j.report(), /\[죽음\]/);
});

test('journal failureDigest groups failures since an index', async () => {
  const { Journal } = await import('../src/journal.js');
  const j = new Journal(tmp());
  j.add('skill_fail', { name: 'a', error: 'e0' });
  const since = j.entries.length;
  j.add('skill_fail', { name: 'a', error: 'e1' });
  j.add('skill_fail', { name: 'a', error: 'e1' });
  j.add('skill_fail', { name: 'b', error: 'x' });
  j.add('death', { cause: 'lava' });
  const d = j.failureDigest(since);
  assert.equal(d.bySkill.a.n, 2);
  assert.deepEqual(d.bySkill.a.errors, ['e1']);
  assert.equal(d.bySkill.b.n, 1);
  assert.deepEqual(d.deaths, ['lava']);
});

test('sandbox rejects un-awaited async api calls', () => {
  assert.throws(() => checkCode('api.gather("log", 3);\nreturn 1;'), /missing await/);
  assert.doesNotThrow(() => checkCode('await api.gather("log", 3);\nreturn api.inventory();'));
  assert.doesNotThrow(() => checkCode('const n = api.countBlocks("log");\nreturn n;'));
});

test('createApi: floating rejected promise does not crash the process', async () => {
  const { createApi } = await import('../src/actions.js');
  const bot = { registry: {}, entity: { position: { floored: () => ({}) } }, pathfinder: { goto: async () => { throw new Error('PathStopped'); } } };
  const api = createApi(bot, { aborted: false });
  api.goTo(1, 2, 3); // not awaited on purpose
  await new Promise((r) => setTimeout(r, 20)); // would crash the test runner if unhandled
});

test('sandbox: unknown api name gives helpful error', () => {
  assert.throws(() => checkCode('await api.collect_logs(3);', ['gather', 'useSkill']), /useSkill/);
  assert.doesNotThrow(() => checkCode('await api.gather("log", 2);', ['gather', 'useSkill']));
});

test('sandbox: Korean outside strings/comments rejected, inside allowed', () => {
  assert.throws(() => checkCode('const 위치 = api.position();'), /Korean/);
  assert.doesNotThrow(() => checkCode('// 위치 확인\nconst p = api.position();\napi.log("위치로 이동");'));
});

test('useSkill runs a saved skill from inside another skill', async () => {
  const { createApi } = await import('../src/actions.js');
  const bot = { registry: {}, entity: {}, pathfinder: {} };
  let api;
  api = createApi(bot, { aborted: false }, () => {}, {
    useSkill: (name, args) => runSkill('return args.x * 2;', api, args, { aborted: false }),
  });
  const out = await runSkill('return await api.useSkill("double", {x: 4});', api, {}, { aborted: false });
  assert.equal(out, 8);
});

test('diffState: outcome-based success', async () => {
  const { diffState } = await import('../src/observe.js');
  const { Vec3 } = await import('vec3');
  const base = { inv: { oak_log: 1 }, pos: new Vec3(0, 64, 0), food: 20, hostiles: 1 };
  assert.equal(diffState(base, { ...base }).effect, false);
  const got = diffState(base, { ...base, inv: { oak_log: 4 } });
  assert.equal(got.effect, true);
  assert.match(got.text, /oak_log \+3/);
  assert.equal(diffState(base, { ...base, pos: new Vec3(5, 64, 0) }).effect, true);
  assert.equal(diffState(base, { ...base, hostiles: 0 }).effect, true);
  assert.equal(diffState(base, { ...base, pos: new Vec3(1, 64, 0) }).effect, false); // tiny wiggle is not progress
});

test('brains: snapshot, rotate, restore', async () => {
  const { Brains } = await import('../src/brain.js');
  const root = tmp();
  const data = path.join(root, 'data');
  fs.mkdirSync(data);
  fs.writeFileSync(path.join(data, 'memory.json'), '{"v":1}');
  const b = new Brains(data, path.join(root, 'brains'), 2);
  const ms = b.snapshot('ms-logs');
  for (let i = 0; i < 4; i++) b.snapshot('auto');
  assert.equal(b.list().filter((n) => n.endsWith('-auto')).length, 2); // rotated
  assert.ok(b.list().includes(ms)); // milestone kept
  fs.writeFileSync(path.join(data, 'memory.json'), '{"v":2}');
  const backup = b.restore(ms);
  assert.equal(fs.readFileSync(path.join(data, 'memory.json'), 'utf8'), '{"v":1}');
  assert.ok(backup.endsWith('-before-restore'));
  assert.throws(() => b.restore('nope'));
});

test('skills.consolidate archives useless/failing skills, never builtins', () => {
  const dir = tmp();
  const s = new Skills(dir);
  s.save('never_works', 'x', {}, 'return 1;');
  s.record('never_works', false);
  s.record('never_works', false);
  s.save('good_one', 'y', {}, 'return 1;');
  s.record('good_one', true);
  for (let i = 0; i < 3; i++) s.record('collect_logs', false); // builtin
  const archived = s.consolidate(25);
  assert.deepEqual(archived, ['never_works']);
  assert.ok(!s.has('never_works') && s.has('good_one') && s.has('collect_logs'));
  assert.ok(fs.existsSync(path.join(dir, 'skills', 'archive', 'never_works.js')));
  // over capacity: lowest score goes first
  s.save('meh', 'z', {}, 'return 1;');
  s.record('meh', true);
  s.record('meh', false);
  assert.deepEqual(s.consolidate(1), ['meh']);
});

test('builtin skills cannot be overwritten and are repaired on load', () => {
  const dir = tmp();
  const s = new Skills(dir);
  assert.throws(() => s.save('collect_logs', 'bad', {}, 'return 0;'), /builtin/);
  // simulate an older version that overwrote a builtin
  const idx = JSON.parse(fs.readFileSync(path.join(dir, 'skills', 'index.json'), 'utf8'));
  idx.collect_logs.builtin = false;
  fs.writeFileSync(path.join(dir, 'skills', 'index.json'), JSON.stringify(idx));
  fs.writeFileSync(path.join(dir, 'skills', 'collect_logs.js'), 'return 0;');
  const s2 = new Skills(dir);
  assert.ok(s2.index.collect_logs.builtin);
  assert.match(s2.code('collect_logs'), /api\.gather\('log'/);
});

test('primitive actions run one at a time even if called in parallel', async () => {
  const { createApi } = await import('../src/actions.js');
  let active = 0;
  let maxActive = 0;
  const bot = {
    registry: {},
    entity: { position: { floored: () => ({}) } },
    pathfinder: {
      goto: async () => {
        active++;
        maxActive = Math.max(maxActive, active);
        await new Promise((r) => setTimeout(r, 10));
        active--;
      },
    },
  };
  const api = createApi(bot, { aborted: false });
  await Promise.all([api.goTo(1, 2, 3), api.goTo(4, 5, 6), api.goTo(7, 8, 9)]);
  assert.equal(maxActive, 1);
});

test('saved skill names pass the API check and must be awaited', () => {
  const names = ['gather', 'useSkill', 'collect_logs'];
  assert.doesNotThrow(() => checkCode('await api.collect_logs({count: 3});', names));
  assert.throws(() => checkCode('api.collect_logs({count: 3});', names), /missing await/);
  assert.throws(() => checkCode('await api.made_up_skill();', names), /does not exist/);
});

test('events: long lines cut on console, repeats collapsed, full text in bot.log', async () => {
  const { Events } = await import('../src/events.js');
  const dir = tmp();
  const ev = new Events({ dataDir: dir, maxChars: 30, clearEvery: 0 });
  const out = [];
  const orig = console.log;
  console.log = (x) => out.push(x);
  try {
    ev.log('x'.repeat(100));
    ev.log('same');
    ev.log('same');
    ev.log('same');
    ev.log('next');
  } finally {
    console.log = orig;
  }
  assert.ok(out[0].endsWith('…') && out[0].length <= 31);
  assert.equal(out.filter((l) => l.includes('same')).length, 1);
  assert.ok(out.some((l) => l.includes('2번 더 반복')));
  const file = fs.readFileSync(path.join(dir, 'bot.log'), 'utf8');
  assert.ok(file.includes('x'.repeat(100)));
  assert.equal(file.split('same').length - 1, 3);
});

test('new body tools: nearbyMobs, placeAt, digAt, equip, block', async () => {
  const { createApi } = await import('../src/actions.js');
  const { Vec3 } = await import('vec3');
  const placed = [];
  const dug = [];
  const equipped = [];
  let shieldUp = 0;
  const blocks = new Map(); // "x,y,z" -> block; default air above y=63, stone at/below
  const blockAt = (p) => blocks.get(`${p.x},${p.y},${p.z}`) ?? (p.y <= 63 ? { name: 'stone', boundingBox: 'block', position: p, diggable: true } : { name: 'air', boundingBox: 'empty', position: p });
  const me = { position: new Vec3(0.5, 64, 0.5) };
  const zombie = { name: 'zombie', type: 'mob', position: new Vec3(5, 64, 0) };
  const cow = { name: 'cow', type: 'mob', position: new Vec3(2, 64, 0) };
  const bot = {
    registry: { items: {} },
    entity: me,
    entities: { 1: me, 2: zombie, 3: cow },
    blockAt,
    inventory: { items: () => [{ name: 'dirt', type: 1 }, { name: 'shield', type: 2 }], slots: {} },
    equip: async (item, dest) => {
      equipped.push([item.name, dest]);
      if (dest === 'off-hand') bot.inventory.slots[45] = item;
    },
    placeBlock: async (ref, face) => placed.push([ref.position.toString(), face.toString()]),
    dig: async (b) => dug.push(b.name),
    activateItem: () => shieldUp++,
    deactivateItem: () => shieldUp--,
    pathfinder: { goto: async () => {} },
  };
  const api = createApi(bot, { aborted: false });

  const mobs = api.nearbyMobs();
  assert.deepEqual(mobs.map((m) => m.name), ['cow', 'zombie']);
  assert.equal(mobs[1].hostile, true);
  assert.equal(mobs[1].x, 5);

  await api.placeAt(1, 64, 0, 'dirt'); // ground below is stone -> attach on top face
  assert.deepEqual(placed[0], [new Vec3(1, 63, 0).toString(), new Vec3(0, 1, 0).toString()]);
  await assert.rejects(api.placeAt(0, 64, 0, 'dirt'), /inside yourself/);
  await assert.rejects(api.placeAt(1, 64, 0, 'diamond_block'), /no diamond_block/);

  assert.equal(await api.digAt(0, 63, 0), 'stone');
  await assert.rejects(api.digAt(0, 70, 0), /nothing to dig/);

  await assert.rejects(api.block(10), /no shield in off-hand/);
  await api.equip('shield', 'off-hand');
  assert.deepEqual(equipped.at(-1), ['shield', 'off-hand']);
  await api.block(10);
  assert.equal(shieldUp, 0); // raised and lowered again
  await assert.rejects(api.equip('dirt', 'hat'), /unknown slot/);
});

test('skills.closest maps made-up names to existing skills', () => {
  const s = new Skills(tmp());
  assert.equal(s.closest('collect_logs_v2_fixed'), 'collect_logs');
  assert.equal(s.closest('collect_logs_v2'), 'collect_logs');
  assert.equal(s.closest('explore_area_safely'), 'explore_area');
  assert.equal(s.closest('eat_some_food'), 'eat_food');
  assert.equal(s.closest('build_nether_portal'), null);
});

test('goto retries once when a stale stop cancels the first move', async () => {
  const { createApi } = await import('../src/actions.js');
  let calls = 0;
  const bot = {
    registry: {},
    entity: { position: { floored: () => ({}) } },
    pathfinder: {
      goto: async () => {
        calls++;
        if (calls === 1) {
          const e = new Error('Path was stopped');
          e.name = 'PathStopped';
          throw e;
        }
      },
    },
  };
  const api = createApi(bot, { aborted: false });
  await api.goTo(1, 2, 3);
  assert.equal(calls, 2);
  // a real failure (NoPath) is not retried
  calls = 0;
  bot.pathfinder.goto = async () => {
    calls++;
    const e = new Error('No path');
    e.name = 'NoPath';
    throw e;
  };
  await assert.rejects(api.goTo(1, 2, 3), /No path/);
  assert.equal(calls, 1);
});

test('craft error explains the missing ingredients with real 26.1 recipes', async () => {
  const { createApi } = await import('../src/actions.js');
  const { createRequire } = await import('node:module');
  const require = createRequire(import.meta.url);
  const reg = require('prismarine-registry')('26.1');
  const Recipe = require('prismarine-recipe')(reg).Recipe;
  const inv = [{ name: 'birch_log', count: 3, type: reg.itemsByName.birch_log.id }];
  const bot = {
    registry: reg,
    entity: { position: { floored: () => ({}) } },
    inventory: { items: () => inv },
    recipesFor: () => [],
    recipesAll: (id, meta, table) => Recipe.find(id, meta).filter((r) => !r.requiresTable || table),
    findBlock: () => null,
    pathfinder: { goto: async () => {} },
  };
  const ctx = { aborted: false };
  const api = createApi(bot, ctx);
  await assert.rejects(api.craft('crafting_table'), (e) => {
    assert.match(e.message, /birch_planks x4 \(have 0\)/);
    assert.match(e.message, /api\.craft\('birch_planks'\)/);
    return true;
  });
  await assert.rejects(api.craft('wooden_pickaxe'), /needs a crafting_table PLACED/);
  assert.equal(ctx.errors.length, 2); // swallowed-or-not, every api error is recorded
});

test('knowledge: plans from real 26.1 game data', async () => {
  const { plan, minTool, recipeInfo, stepToAction, knowledgeText } = await import('../src/knowledge.js');
  const { createRequire } = await import('node:module');
  const reg = createRequire(import.meta.url)('prismarine-registry')('26.1');

  // tools: easiest tool of the needed level
  assert.equal(minTool(reg, 'stone'), 'wooden_pickaxe');
  assert.equal(minTool(reg, 'iron_ore'), 'stone_pickaxe'); // not copper (needs smelting)
  assert.equal(minTool(reg, 'diamond_ore'), 'iron_pickaxe');
  assert.equal(minTool(reg, 'oak_log'), null);

  // recipes prefer the wood we hold
  assert.deepEqual(recipeInfo(reg, 'crafting_table', { birch_log: 2 }).ingredients, { birch_planks: 4 });

  // empty hands -> wooden pickaxe: logs first, table placed before the pickaxe
  const wp = plan(reg, {}, 'wooden_pickaxe', 1);
  assert.equal(wp[0].do, 'gather');
  assert.deepEqual(stepToAction(wp[0]), { skill: 'collect_logs', args: { count: 3 } });
  const placeIdx = wp.findIndex((s) => s.do === 'place' && s.block === 'crafting_table');
  const pickIdx = wp.findIndex((s) => s.do === 'craft' && s.item === 'wooden_pickaxe');
  assert.ok(placeIdx >= 0 && placeIdx < pickIdx);

  // stone pickaxe uses cobblestone from stone, not cobbled_deepslate
  const sp = plan(reg, { wooden_pickaxe: 1, oak_planks: 2 }, 'stone_pickaxe', 1);
  assert.ok(sp.some((s) => s.do === 'gather' && s.block === 'stone'));
  assert.ok(!sp.some((s) => s.do === 'unknown'));

  // iron ingot: mine iron_ore, build+place furnace, smelt
  const ii = plan(reg, { stone_pickaxe: 1, cobblestone: 8, crafting_table: 1 }, 'iron_ingot', 3);
  assert.deepEqual(stepToAction(ii[0]), { skill: 'mine_block', args: { block: 'iron_ore', count: 3 } });
  assert.ok(ii.some((s) => s.do === 'place' && s.block === 'furnace'));
  assert.equal(ii.at(-1).do, 'smelt');

  // diamond from a stone pickaxe: full chain, nothing unknown, ends with mining diamond_ore
  const di = plan(reg, { stone_pickaxe: 1 }, 'diamond', 1);
  assert.ok(!di.some((s) => s.do === 'unknown'));
  assert.equal(di.at(-1).block, 'diamond_ore');

  // already have it -> empty plan
  assert.deepEqual(plan(reg, { furnace: 1 }, 'furnace', 1), []);
  assert.match(knowledgeText(reg), /diamond_ore=iron_pickaxe/);
});

test('ore matcher also accepts the deepslate variant; builtin seeds include mine_block/smelt_item', async () => {
  const s = new Skills(tmp());
  assert.ok(s.has('mine_block') && s.has('smelt_item'));
  assert.ok(Skills.isBuiltin('mine_block'));
});

test('gather matcher: iron_ore also matches deepslate_iron_ore', async () => {
  const { createApi } = await import('../src/actions.js');
  let matching;
  const bot = { registry: {}, entity: {}, findBlocks: (o) => ((matching = o.matching), []) };
  createApi(bot, { aborted: false }).countBlocks('iron_ore');
  assert.ok(matching({ name: 'iron_ore' }) && matching({ name: 'deepslate_iron_ore' }));
  assert.ok(!matching({ name: 'gold_ore' }));
});

// ---- tiny simulated world for movement tools ----
async function fakeWorld({ solidBelow = 64, overrides = {}, start = [0.5, 64, 0.5], items = [] } = {}) {
  const { Vec3 } = await import('vec3');
  const blocks = new Map(Object.entries(overrides));
  const key = (p) => `${p.x},${p.y},${p.z}`;
  const blockAt = (p) => {
    p = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z));
    const name = blocks.get(key(p)) ?? (p.y < solidBelow ? 'stone' : 'air');
    const open = name === 'air' || name === 'cave_air' || name === 'water' || name.includes('lava');
    return { name, position: p, boundingBox: open ? 'empty' : 'block', diggable: name !== 'bedrock', material: 'mineable/pickaxe' };
  };
  const me = { position: new Vec3(...start) };
  const fall = () => {
    while (blockAt(me.position.offset(0, -1, 0)).boundingBox === 'empty' && me.position.y > -64) me.position.y -= 1;
  };
  const bot = {
    registry: { items: {}, blocksByName: { cave_air: { id: 999 } } },
    entity: me,
    entities: {},
    blockAt,
    inventory: { items: () => items, slots: {} },
    equip: async () => {},
    dig: async (b) => {
      blocks.set(key(b.position), 'air');
      fall();
    },
    placeBlock: async (ref, face) => blocks.set(key(ref.position.plus(face)), 'cobblestone'),
    pathfinder: {
      goto: async (g) => {
        me.position = new Vec3(g.x + 0.5, g.y, g.z + 0.5);
        fall();
      },
    },
    findBlocks: () => [],
  };
  return { bot, blocks, Vec3 };
}

test('descend: staircase down to target y, stops at lava', async () => {
  const { createApi } = await import('../src/actions.js');
  const { bot } = await fakeWorld();
  const y = await createApi(bot, { aborted: false }).descend(58);
  assert.ok(y <= 58, `reached y=${y}`);

  // lava all around below -> refuses to walk into it
  const lava = {};
  for (let x = -3; x <= 3; x++) for (let z = -3; z <= 3; z++) lava[`${x},61,${z}`] = 'lava';
  const w2 = await fakeWorld({ overrides: lava });
  await assert.rejects(createApi(w2.bot, { aborted: false }).descend(50), /lava/);
});

test('shelter: digs 3 down, closes the roof, second call is a no-op', async () => {
  const { createApi } = await import('../src/actions.js');
  const { bot, blocks } = await fakeWorld({ items: [{ name: 'cobblestone', type: 1 }] });
  const api = createApi(bot, { aborted: false });
  const pos = await api.shelter();
  assert.equal(pos.y, 61);
  assert.equal(blocks.get('0,63,0'), 'cobblestone'); // roof above the head
  assert.equal(await api.shelter(), 'already sheltered');

  // no ground (standing over a cave) -> refuses
  const w2 = await fakeWorld({ overrides: { '0,62,0': 'cave_air' }, items: [{ name: 'dirt', type: 1 }] });
  await assert.rejects(createApi(w2.bot, { aborted: false }).shelter(), /cannot dig a shelter/);
});

test('exploreCave walks to cave air with a floor; ore hint points to the right depth', async () => {
  const { createApi } = await import('../src/actions.js');
  const { oreHint } = await import('../src/knowledge.js');
  const { bot, Vec3 } = await fakeWorld({ overrides: { '10,40,0': 'cave_air' } });
  bot.findBlocks = () => [new Vec3(10, 40, 0)];
  const at = await createApi(bot, { aborted: false }).exploreCave();
  assert.deepEqual(at, { x: 10, y: 40, z: 0 });
  bot.findBlocks = () => [];
  await assert.rejects(createApi(bot, { aborted: false }).exploreCave(), /no cave nearby/);
  assert.match(oreHint('diamond_ore', 70), /descend_mine \{y: -58\}/);
  assert.match(oreHint('iron_ore', 20), /explore/);
});

test('self-chosen goals: pick, keep, complete, and give up', async () => {
  const { Goals } = await import('../src/goals.js');
  const { Journal } = await import('../src/journal.js');
  const dir = tmp();
  const bot = { registry: { itemsByName: { iron_chestplate: {} } } };
  let asked = 0;
  const llm = { ask: async () => (asked++, '{"goal":"철 갑옷 만들기","item":"iron_chestplate","count":1}') };
  const memory = new Memory(dir);
  const goals = new Goals({ bot, llm, memory, journal: new Journal(dir), events: { log() {} }, config: { freeGoalMaxSteps: 2 } });
  const base = { iron_pickaxe: 1 }; // fixed tool path done -> free goals
  const obs = { inv: base, text: '상태' };

  const g1 = await goals.current(obs);
  assert.equal(g1.text, '철 갑옷 만들기');
  assert.deepEqual(g1.target, { item: 'iron_chestplate', count: 1 });
  await goals.current(obs); // kept, no new question
  assert.equal(asked, 1);
  await goals.current({ ...obs, inv: { ...base, iron_chestplate: 1 } }); // done -> asks for a new goal
  assert.equal(asked, 2);
  assert.equal(memory.data.goalHistory.at(-1).result, '달성');
  await goals.current(obs);
  await goals.current(obs);
  await goals.current(obs); // over freeGoalMaxSteps -> dropped, new goal
  assert.equal(memory.data.goalHistory.at(-1).result, '포기');
  // unknown item from the model -> goal without a target, not a crash
  llm.ask = async () => '{"goal":"집 짓기","item":"house","count":1}';
  memory.data.freeGoal = null;
  assert.equal((await goals.current(obs)).target, null);
});

test('reading the goal (advance:false) neither spends a turn nor asks the model', async () => {
  const { Goals } = await import('../src/goals.js');
  const { Journal } = await import('../src/journal.js');
  const dir = tmp();
  let asked = 0;
  const llm = { ask: async () => (asked++, '{"goal":"침대 만들기","item":"white_bed","count":1}') };
  const memory = new Memory(dir);
  const goals = new Goals({ bot: { registry: { itemsByName: { white_bed: {} } } }, llm, memory, journal: new Journal(dir), events: { log() {} }, config: { freeGoalMaxSteps: 80 } });
  const obs = { inv: { iron_pickaxe: 1 }, text: '' };
  const g0 = await goals.current(obs, { advance: false }); // no goal yet: no LLM call
  assert.equal(asked, 0);
  assert.equal(g0.id, 'free');
  await goals.current(obs); // a real turn picks one
  const steps = memory.data.freeGoal.steps;
  for (let i = 0; i < 5; i++) await goals.current(obs, { advance: false });
  assert.equal(memory.data.freeGoal.steps, steps);
  assert.equal(asked, 1);
});

test('writeSkill: a reply that fails before running never reuses an older run\'s logs', async () => {
  const { Agent } = await import('../src/agent.js');
  const { Journal } = await import('../src/journal.js');
  const { EventEmitter } = await import('node:events');
  const dir = tmp();
  const bot = Object.assign(new EventEmitter(), { registry: {}, inventory: { items: () => [] } });
  const sent = [];
  const llm = { ask: async (tier, sys, msgs) => (sent.push(msgs.map((m) => m.content)), 'no NAME here') };
  const agent = new Agent({ bot, llm, skills: new Skills(dir), memory: new Memory(dir), events: { log() {}, update() {} }, journal: new Journal(dir), config: { dataDir: dir, brainsDir: path.join(dir, 'b') } });
  const out = await agent.writeSkill('나무 캐기', { text: '' }, { text: '' }, 2);
  assert.match(out, /2번 실패/);
  const feedback = sent[1].at(-1);
  assert.match(feedback, /실행하기 전에 실패/);
  assert.doesNotMatch(feedback, /api\.log 출력/);
});

test('bed: planner hunts sheep for wool instead of looping through dye recipes', async () => {
  const { plan, stepToAction } = await import('../src/knowledge.js');
  const { createRequire } = await import('node:module');
  const reg = createRequire(import.meta.url)('prismarine-registry')('26.1');
  const p = plan(reg, { stone_pickaxe: 1 }, 'white_bed', 1);
  assert.ok(p.length <= 8, `plan has ${p.length} steps`);
  assert.ok(!p.some((s) => s.do === 'unknown'));
  assert.deepEqual(stepToAction(p[0]), { skill: 'hunt_animal', args: { mob: 'sheep', count: 3 } });
  assert.equal(p.at(-1).item, 'white_bed');
  // cooked food chain: cow -> furnace
  const food = plan(reg, {}, 'cooked_beef', 1);
  assert.ok(food.some((s) => s.do === 'hunt' && s.mob === 'cow') && food.at(-1).do === 'smelt');
});

test('sleepInBed: places the bed from inventory, refuses in daytime, sleeps at night', async () => {
  const { createApi } = await import('../src/actions.js');
  const { bot, blocks, Vec3 } = await fakeWorld({ items: [{ name: 'white_bed', type: 5 }] });
  bot.placeBlock = async (ref, face) => blocks.set(`${ref.position.x + face.x},${ref.position.y + face.y},${ref.position.z + face.z}`, 'white_bed');
  bot.lookAt = async () => {};
  bot.findBlock = ({ matching }) => {
    for (const [k, name] of blocks) if (matching({ name })) return { name, position: new Vec3(...k.split(',').map(Number)) };
    return null;
  };
  bot.time = { timeOfDay: 6000 };
  const api = createApi(bot, { aborted: false });
  await assert.rejects(api.sleepInBed(), /only sleep at night/);
  assert.ok([...blocks.values()].includes('white_bed')); // bed got placed anyway, ready for tonight

  bot.time = { timeOfDay: 18000 };
  let slept = false;
  bot.sleep = async () => {
    slept = true;
    bot.isSleeping = true;
    setTimeout(() => (bot.isSleeping = false), 30); // morning
  };
  const r = await api.sleepInBed();
  assert.ok(slept && r.slept && !r.stillSleeping);
});

test('attack: errors when the mob is not around, walks onto the kill spot for drops', async () => {
  const { createApi } = await import('../src/actions.js');
  const { bot, Vec3 } = await fakeWorld();
  bot.nearestEntity = () => null;
  await assert.rejects(createApi(bot, { aborted: false }).attack('sheep'), /no sheep within 32/);

  const sheep = { name: 'sheep', height: 1.3, position: new Vec3(2, 64, 0) };
  let alive = true;
  bot.nearestEntity = (f) => (alive && f(sheep) ? sheep : null);
  bot.lookAt = async () => {};
  bot.attack = () => (alive = false);
  const gotos = [];
  const orig = bot.pathfinder.goto;
  bot.pathfinder.goto = async (g) => (gotos.push([g.x, g.y, g.z]), orig(g));
  assert.equal(await createApi(bot, { aborted: false }).attack('sheep'), true);
  assert.deepEqual(gotos.at(-1), [2, 64, 0]);
});

test('one full decision turn: observe -> plan -> model picks a skill -> run -> journal', async () => {
  const { Agent } = await import('../src/agent.js');
  const { Journal } = await import('../src/journal.js');
  const { EventEmitter } = await import('node:events');
  const { createRequire } = await import('node:module');
  const { Vec3 } = await import('vec3');
  const reg = createRequire(import.meta.url)('prismarine-registry')('26.1');
  const dir = tmp();
  const pos = new Vec3(0.5, 64, 0.5);
  const bot = Object.assign(new EventEmitter(), {
    registry: reg,
    entity: { position: pos },
    entities: {},
    health: 20,
    food: 20,
    time: { timeOfDay: 1000 },
    inventory: { items: () => [] },
    findBlocks: () => [],
    findBlock: () => null,
    blockAt: () => null,
    nearestEntity: () => null,
    pathfinder: { setGoal() {}, goto: async () => {} },
    clearControlStates() {},
  });
  const prompts = [];
  const llm = {
    budgetLeft: () => true,
    spentUsd: 0,
    ask: async (tier, sys, user) => (prompts.push(user), '{"thought":"원목부터","skill":"collect_logs","args":{"count":1}}'),
  };
  const journal = new Journal(dir);
  const agent = new Agent({ bot, llm, skills: new Skills(dir), memory: new Memory(dir), events: { log() {}, update() {} }, journal, config: { dataDir: dir, brainsDir: path.join(dir, 'b'), skillTimeoutMs: 5000, freeGoalMaxSteps: 80 } });
  await agent.step();
  assert.match(prompts[0], /추천 다음 행동: \{"skill":"collect_logs"/); // planner reached the prompt
  assert.equal(agent.lastAction, 'collect_logs({"count":1})');
  assert.match(agent.lastResult, /^실패: no log within 48 blocks/); // ran for real, nothing around
  assert.equal(journal.entries.at(-1).type, 'skill_fail');
});

test('craft only asks mineflayer for as many crafts as the ingredients allow', async () => {
  const { createApi } = await import('../src/actions.js');
  const inv = [{ name: 'oak_log', type: 10, count: 2 }];
  let asked = null;
  const logs = [];
  const bot = {
    registry: { itemsByName: { oak_planks: { id: 20 } }, items: {} },
    entity: { position: { floored: () => ({}) } },
    inventory: { items: () => inv },
    recipesFor: () => [{ delta: [{ id: 10, count: -1 }, { id: 20, count: 4 }], requiresTable: false }],
    craft: async (r, n) => {
      asked = n;
      inv[0].count -= n;
      inv.push({ name: 'oak_planks', type: 20, count: 4 * n });
    },
  };
  const api = createApi(bot, { aborted: false }, (m) => logs.push(m));
  assert.equal(await api.craft('oak_planks', 4), 8); // 2 logs -> only 2 crafts
  assert.equal(asked, 2);
  assert.match(logs[0], /asked for 4 crafts, ingredients only allow 2/);

  bot.craft = async () => {
    throw new Error('Error: Event updateSlot:0 did not fire within timeout of 20000ms');
  };
  inv[0].count = 1;
  await assert.rejects(api.craft('oak_planks', 1), /got stuck \(server did not answer\)/);
});

test('place clears leaf litter / grass in the target cell before placing', async () => {
  const { createApi } = await import('../src/actions.js');
  const { Vec3 } = await import('vec3');
  const cells = new Map([['1,64,0', 'leaf_litter']]);
  const order = [];
  const blockAt = (p) => {
    const name = cells.get(`${p.x},${p.y},${p.z}`) ?? (p.y < 64 ? 'grass_block' : 'air');
    const open = name !== 'grass_block';
    return { name, position: p, boundingBox: open ? 'empty' : 'block' };
  };
  const bot = {
    registry: {},
    entity: { position: new Vec3(0.5, 64, 0.5) },
    blockAt,
    inventory: { items: () => [{ name: 'furnace', type: 1 }] },
    equip: async () => {},
    dig: async (b) => {
      order.push(`dig ${b.name}`);
      cells.delete(`${b.position.x},${b.position.y},${b.position.z}`);
    },
    placeBlock: async (ref, face) => {
      const t = ref.position.plus(face);
      order.push(`place at ${t.x},${t.y},${t.z} (was ${blockAt(t).name})`);
    },
  };
  await createApi(bot, { aborted: false }).place('furnace');
  assert.deepEqual(order, ['dig leaf_litter', 'place at 1,64,0 (was air)']);
});

test('place: when every cell next to the bot is blocked, walks a few blocks and places there', async () => {
  const { createApi } = await import('../src/actions.js');
  const { bot, blocks } = await fakeWorld({ items: [{ name: 'furnace', type: 1 }] });
  // wall the bot in: all 8 neighbours at feet level are solid
  for (const [x, z] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]]) blocks.set(`${x},64,${z}`, 'stone');
  let placedAt = null;
  bot.placeBlock = async (ref, face) => (placedAt = ref.position.plus(face));
  const t = await createApi(bot, { aborted: false }).place('furnace');
  assert.ok(t && placedAt && placedAt.equals(t));
  assert.ok(Math.abs(t.x) + Math.abs(t.z) >= 2);
});

test('skill list shows builtins first, then learned skills with their record', () => {
  const s = new Skills(tmp());
  s.save('go_to_logs_v2', '원목으로 가기', {}, 'return 1;');
  const text = s.describe();
  const builtinAt = text.indexOf('[기본 스킬');
  const learnedAt = text.indexOf('[직접 배운 스킬]');
  assert.ok(builtinAt === 0 && learnedAt > text.indexOf('- collect_logs'));
  assert.ok(text.indexOf('- go_to_logs_v2') > learnedAt);
  assert.match(text, /go_to_logs_v2.*\[성공 0\/실패 0\]/);
  assert.doesNotMatch(text.split('[직접 배운 스킬]')[0], /\[성공/); // builtins show no stats
});

test('a learned skill that fails 5 times in a row is archived on the spot', async () => {
  const { Agent } = await import('../src/agent.js');
  const { Journal } = await import('../src/journal.js');
  const { EventEmitter } = await import('node:events');
  const { Vec3 } = await import('vec3');
  const dir = tmp();
  const bot = Object.assign(new EventEmitter(), {
    registry: {},
    entity: { position: new Vec3(0, 64, 0) },
    entities: {},
    food: 20,
    inventory: { items: () => [] },
    pathfinder: { setGoal() {} },
    clearControlStates() {},
  });
  const skills = new Skills(dir);
  skills.save('craft_item_with_table', 'x', {}, 'return 1;'); // never does anything
  skills.record('craft_item_with_table', true); // old loose "success" from before
  const agent = new Agent({ bot, llm: {}, skills, memory: new Memory(dir), events: { log() {}, update() {} }, journal: new Journal(dir), config: { dataDir: dir, brainsDir: path.join(dir, 'b'), skillTimeoutMs: 1000 } });
  for (let i = 0; i < 4; i++) await agent.useSkill('craft_item_with_table', {});
  assert.ok(skills.has('craft_item_with_table'));
  await agent.useSkill('craft_item_with_table', {});
  assert.ok(!skills.has('craft_item_with_table'));
  assert.ok(fs.existsSync(path.join(dir, 'skills', 'archive', 'craft_item_with_table.js')));
  // builtins are never archived, however often they fail
  for (let i = 0; i < 6; i++) await agent.useSkill('eat_food', {});
  assert.ok(skills.has('eat_food'));
});

test('builtin skill updates (description/code) reach an existing install, stats are kept', () => {
  const dir = tmp();
  const s = new Skills(dir);
  s.record('night_shelter', true);
  const idxFile = path.join(dir, 'skills', 'index.json');
  const idx = JSON.parse(fs.readFileSync(idxFile, 'utf8'));
  idx.night_shelter.description = '옛날 설명';
  fs.writeFileSync(idxFile, JSON.stringify(idx));
  fs.writeFileSync(path.join(dir, 'skills', 'night_shelter.js'), 'return "old";');
  const s2 = new Skills(dir);
  assert.match(s2.index.night_shelter.description, /위급할 때/);
  assert.match(s2.code('night_shelter'), /api\.shelter/);
  assert.equal(s2.index.night_shelter.uses, 1);
});

test('decision prompt lets the bot work at night', async () => {
  const { DECISION_SYSTEM, REFLECT_SYSTEM } = await import('../src/prompts.js');
  assert.match(DECISION_SYSTEM, /밤에도 쉬지 않고 계속 작업/);
  assert.doesNotMatch(DECISION_SYSTEM, /아침까지 쉰다/);
  assert.doesNotMatch(REFLECT_SYSTEM, /밤에는 .*대기/);
});

test('pointless skill names: builtin copies and check-only skills are refused without trying', async () => {
  assert.match(Skills.pointlessName('smelt_item_v2'), /copy of the builtin skill "smelt_item"/);
  assert.match(Skills.pointlessName('collect_logs_fixed'), /"collect_logs"/);
  assert.match(Skills.pointlessName('check_furnace_and_materials'), /only checks something/);
  assert.equal(Skills.pointlessName('build_iron_armor'), null);
  assert.equal(Skills.pointlessName('collect_logs'), null); // the builtin itself is handled elsewhere

  const { Agent } = await import('../src/agent.js');
  const { Journal } = await import('../src/journal.js');
  const { EventEmitter } = await import('node:events');
  const dir = tmp();
  let calls = 0;
  const llm = { ask: async () => (calls++, 'NAME: smelt_item_v2\nDESCRIPTION: x\nARGS: {}\nCODE:\n```js\nreturn 1;\n```') };
  const bot = Object.assign(new EventEmitter(), { registry: {}, inventory: { items: () => [] } });
  const agent = new Agent({ bot, llm, skills: new Skills(dir), memory: new Memory(dir), events: { log() {}, update() {} }, journal: new Journal(dir), config: { dataDir: dir, brainsDir: path.join(dir, 'b') } });
  const out = await agent.writeSkill('굽기 고치기', { text: '' }, { text: '' });
  assert.match(out, /^스킬 작성 안 함: .*smelt_item/);
  assert.equal(calls, 1); // stopped after the first reply instead of 3 attempts
});

test('unknown item names get real suggestions', async () => {
  const { similarItems } = await import('../src/knowledge.js');
  const { createRequire } = await import('node:module');
  const reg = createRequire(import.meta.url)('prismarine-registry')('26.1');
  assert.ok(similarItems(reg, 'iron_chest_plate').includes('iron_chestplate'));
  assert.ok(similarItems(reg, 'iron_gardener').some((n) => n.startsWith('iron_')));
  const { createApi } = await import('../src/actions.js');
  const api = createApi({ registry: reg, entity: {}, inventory: { items: () => [] } }, { aborted: false });
  await assert.rejects(api.craft('iron_chest_plate'), /did you mean: .*iron_chestplate/);
});

test('smelt: clears leftovers, stops with a reason when the furnace stalls', async () => {
  const { createApi } = await import('../src/actions.js');
  const slots = { input: { type: 99 }, fuel: null, output: { type: 7, count: 2 } };
  const taken = [];
  const furnace = {
    inputItem: () => slots.input,
    fuelItem: () => slots.fuel,
    outputItem: () => slots.output,
    takeOutput: async () => (taken.push('output'), (slots.output = null), { count: 0 }),
    takeInput: async () => (taken.push('input'), (slots.input = null)),
    takeFuel: async () => (slots.fuel = null),
    putFuel: async () => {},
    putInput: async () => {},
    close() {},
  };
  const bot = {
    registry: { blocksByName: { furnace: { id: 1 } } },
    entity: { position: { floored: () => ({}) } },
    findBlock: () => ({ position: { x: 0, y: 64, z: 0 } }),
    inventory: { items: () => [{ name: 'raw_iron', type: 5, count: 1 }, { name: 'coal', type: 6, count: 1 }] },
    pathfinder: { goto: async () => {} },
    openFurnace: async () => furnace,
  };
  const realNow = Date.now;
  let t = realNow();
  Date.now = () => (t += 4000); // time flies: each poll looks like 4s later
  try {
    await assert.rejects(createApi(bot, { aborted: false }).smelt('raw_iron', 1, 'coal'), /furnace stopped smelting after 0\/1/);
  } finally {
    Date.now = realNow;
  }
  assert.deepEqual(taken, ['output', 'input']); // leftovers cleared before putting ours in
  await assert.rejects(createApi(bot, { aborted: false }).smelt('raw_iron', 3, 'coal'), /need 3 raw_iron to smelt \(have 1\)/);
});

test('smelt: a furnace that never opens fails fast with a reason (not the 120s skill timeout)', async () => {
  const { createApi } = await import('../src/actions.js');
  const bot = {
    registry: { blocksByName: { furnace: { id: 1 } } },
    entity: { position: { floored: () => ({}) } },
    findBlock: () => ({ position: { x: 0, y: 64, z: 0 } }),
    inventory: { items: () => [{ name: 'raw_iron', type: 5, count: 1 }, { name: 'coal', type: 6, count: 1 }] },
    pathfinder: { goto: async () => {} },
    openFurnace: () => new Promise(() => {}), // never opens
  };
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms, ...a) => realSetTimeout(fn, Math.min(ms, 5), ...a); // speed up timers
  try {
    await assert.rejects(createApi(bot, { aborted: false }).smelt('raw_iron', 1, 'coal'), /the furnace did not open/);
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }
});

test('wiki notes: only the ones relevant to the situation, size-limited', async () => {
  const { wikiFor, WIKI_TOPICS } = await import('../src/wiki.js');
  const { createRequire } = await import('node:module');
  const reg = createRequire(import.meta.url)('prismarine-registry')('26.1');
  assert.ok(WIKI_TOPICS.length >= 15);
  const creeper = wikiFor('주변 생물: creeper(적대)@4', reg);
  assert.match(creeper, /크리퍼/);
  assert.doesNotMatch(creeper, /엔더맨/);
  const armor = wikiFor('현재 목표: 철 갑옷 만들기\niron_chestplate', reg);
  assert.match(armor, /iron_chestplate\) 철 8/);
  // words that are in every observation must not pull notes in by themselves
  assert.equal(wikiFor('위치: (0, 64, 0) / 체력 20/20 / 허기 20/20\n인벤토리: {"iron_pickaxe":1,"crafting_table":1}', reg), '');
  const food = wikiFor('허기 4/20\n배고픔', reg);
  assert.match(food, /회복량 좋은 음식\(게임 데이터/);
  assert.match(food, /cooked_beef|cooked_porkchop/);
  assert.equal(wikiFor('아무 관련 없는 말', reg), '');
  const many = wikiFor('creeper skeleton zombie spider enderman lava diamond armor shield bed chest nether enchant food', reg);
  assert.ok(many.split('\n- ').length - 1 <= 3 && many.length < 1100);
});
