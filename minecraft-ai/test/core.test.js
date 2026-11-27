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
