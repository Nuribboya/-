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
