import fs from 'node:fs';
import path from 'node:path';
import { parseJson } from './llm.js';
import { observe, inventoryOf, snapshotState, diffState } from './observe.js';
import { nextGoal } from './curriculum.js';
import { createApi } from './actions.js';
import { runSkill } from './sandbox.js';
import { Skills } from './skills.js';
import { Journal } from './journal.js';
import { Brains } from './brain.js';
import { plan as makePlan, stepText, stepToAction, knowledgeText } from './knowledge.js';
import { DECISION_SYSTEM, WRITER_SYSTEM, REFLECT_SYSTEM, parseWriterReply } from './prompts.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const short = (v, n = 400) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s && s.length > n ? s.slice(0, n) + '…' : s;
};

export class Agent {
  constructor({ bot, llm, skills, memory, events, config, journal }) {
    Object.assign(this, { bot, llm, skills, memory, events, config });
    this.journal = journal ?? new Journal(config.dataDir);
    this.lastSelfMsg = { text: '', at: 0 };
    this.steps = 0;
    this.writeFailStreak = 0;
    this.writeCooldown = 0;
    this.reflections = 0;
    this.brains = new Brains(config.dataDir, config.brainsDir);
    this.lastSnapshotAt = Date.now();
    this.reflectedAt = this.journal.entries.length;
    this.lastHealth = bot.health ?? 20;
    this.running = false;
    this.lastResult = '아직 없음';
    this.lastAction = '';

    // Death message from the server ("<name> was slain by Zombie") tells us the cause.
    bot.on('messagestr', (msg) => {
      if (msg.startsWith(bot.username)) this.lastSelfMsg = { text: msg, at: Date.now() };
    });

    // Damage log: big hits only, with the nearest hostile as the likely source.
    bot.on('health', () => {
      const drop = this.lastHealth - bot.health;
      this.lastHealth = bot.health;
      if (drop >= 3 && bot.health > 0) {
        this.journal.add('damage', { amount: Math.round(drop), source: this.#nearestThreat(), where: this.#where() });
      }
    });

    bot.on('death', () => {
      const night = this.#isNight();
      const where = this.#where();
      const lastAction = this.lastAction;
      const lastResult = short(this.lastResult, 120);
      this.memory.addDeath();
      this.events.update({ deaths: this.memory.data.deaths });
      // The chat line with the cause can arrive just after the death event.
      setTimeout(() => {
        const recent = Date.now() - this.lastSelfMsg.at < 6000;
        const cause = recent ? this.lastSelfMsg.text.slice(bot.username.length + 1) : this.#nearestThreat() ?? '원인불명';
        this.journal.add('death', { cause, where, night, lastAction, lastResult });
        this.memory.addLesson(`죽음 #${this.memory.data.deaths}: ${cause} (${night ? '밤' : '낮'}, ${where}), 직전 행동 "${lastAction}"`);
        this.events.log(`💀 죽었다: ${cause}`);
      }, 1000);
    });
  }

  /** Save failed attempts to data/failed/ (last 30) so they can be inspected later. */
  #keepFailed(parsed, err) {
    try {
      const dir = path.join(this.config.dataDir, 'failed');
      fs.mkdirSync(dir, { recursive: true });
      const t = new Date().toISOString().replace(/[:.]/g, '-');
      fs.writeFileSync(
        path.join(dir, `${t}-${parsed.name}.js`),
        `// ${parsed.description}\n// args: ${JSON.stringify(parsed.args)}\n// error: ${err.replace(/\n/g, ' ')}\n\n${parsed.code}\n`,
      );
      const files = fs.readdirSync(dir).sort();
      for (const f of files.slice(0, Math.max(0, files.length - 30))) fs.rmSync(path.join(dir, f));
    } catch {}
  }

  #snapshot(label) {
    try {
      const name = this.brains.snapshot(label);
      this.lastSnapshotAt = Date.now();
      if (name) this.events.log(`💾 두뇌 저장: ${name}`);
      return name;
    } catch (e) {
      this.events.log(`⚠️ 두뇌 저장 실패: ${e.message}`);
      return null;
    }
  }

  #where() {
    const p = this.bot.entity?.position;
    return p ? `${p.x.toFixed(0)},${p.y.toFixed(0)},${p.z.toFixed(0)}` : '?';
  }

  #isNight() {
    const t = this.bot.time?.timeOfDay ?? 0;
    return t >= 13000 && t < 23000;
  }

  #nearestThreat() {
    const me = this.bot.entity;
    if (!me) return undefined;
    const e = this.bot.nearestEntity((x) => x.name && x.position.distanceTo(me.position) < 6 && x !== me && x.type !== 'object' && x.type !== 'orb');
    return e?.name;
  }

  stop() {
    this.running = false;
  }

  async run() {
    this.running = true;
    while (this.running) {
      if (!this.llm.budgetLeft()) {
        this.events.update({ thought: '💤 오늘 예산을 다 써서 잠시 쉬는 중' });
        await sleep(60000);
        continue;
      }
      try {
        await this.step();
      } catch (e) {
        this.lastResult = `내부 오류: ${e.message}`;
        this.events.log(`⚠️ ${e.message}`);
        await sleep(5000);
      }
      this.events.update({ spentUsd: this.llm.spentUsd, skills: this.skills.count() });
      if (Date.now() - this.lastSnapshotAt > this.config.snapshotEveryMin * 60000) {
        this.#snapshot('auto');
      }
      if (++this.steps % this.config.reflectEvery === 0) {
        try {
          await this.reflect();
        } catch (e) {
          this.events.log(`⚠️ 반성 실패: ${e.message}`);
        }
      }
      await sleep(this.config.decisionIntervalMs);
    }
  }

  async step() {
    const obs = observe(this.bot);
    const before = new Set(this.memory.data.achieved);
    const goal = nextGoal(obs.inv, this.memory);
    for (const id of this.memory.data.achieved) {
      if (!before.has(id)) {
        this.journal.add('milestone', { id });
        this.events.log(`🏁 목표 달성: ${id}`);
        this.#snapshot(`ms-${id}`);
      }
    }
    const lessons = this.memory.data.lessons;

    const user = [
      `현재 목표: ${goal.text}`,
      obs.text,
      `직전 행동: ${this.lastAction || '없음'}`,
      `직전 결과: ${short(this.lastResult)}`,
      this.#planText(goal, obs.inv),
      `보유 스킬:\n${this.skills.describe()}`,
      this.writeCooldown > 0
        ? `⛔ 스킬 작성이 연속 실패해서 앞으로 ${this.writeCooldown}턴 동안 write_skill 금지. 반드시 보유 스킬 중 하나를 "skill"로 실행할 것 (예: collect_logs, craft_item, place_block, explore_area).`
        : '',
      `최근 일지:\n${this.journal.recentText(8) || '- 없음'}`,
      this.journal.deathSummary() ? `누적: ${this.journal.deathSummary()}` : '',
      `교훈:\n${lessons.length ? lessons.map((l) => '- ' + l).join('\n') : '- 아직 없음'}`,
    ].join('\n\n');

    this.system ??= `${DECISION_SYSTEM}\n\n${knowledgeText(this.bot.registry)}`;
    const reply = await this.llm.ask('fast', this.system, user, { maxTokens: 1500 });
    const d = parseJson(reply);
    this.events.update({ thought: d.thought ?? '', goal: goal.text });
    this.events.log(`🧠 ${d.thought ?? ''}`);

    if (this.writeCooldown > 0) this.writeCooldown--;
    if (d.write_skill && this.writeCooldown > 0) {
      // Model ignored the ban: don't burn another 3 attempts, nudge it back to existing skills.
      this.lastAction = 'write_skill (금지 중이라 무시됨)';
      this.lastResult = `write_skill은 ${this.writeCooldown}턴 더 금지. 보유 스킬을 "skill"로 실행할 것`;
      this.events.log('⛔ 스킬 작성 금지 중 - 기존 스킬을 쓰도록 유도');
    } else if (d.write_skill) {
      this.lastAction = `write_skill: ${short(d.write_skill, 80)}`;
      this.lastResult = await this.writeSkill(d.write_skill, obs, goal);
      if (this.lastResult.startsWith('새 스킬')) {
        this.writeFailStreak = 0;
      } else if (++this.writeFailStreak >= 2) {
        this.writeCooldown = this.config.writeCooldownSteps;
        this.writeFailStreak = 0;
        this.events.log(`⛔ 스킬 작성 연속 실패 → ${this.writeCooldown}턴 동안 기존 스킬만 사용`);
      }
    } else if (d.skill) {
      this.lastAction = `${d.skill}(${short(d.args ?? {}, 80)})`;
      this.lastResult = await this.useSkill(d.skill, d.args ?? {});
    } else {
      this.lastAction = 'wait';
      this.lastResult = '대기함';
    }
    this.events.update({ lastAction: this.lastAction });
  }

  async #exec(code, args, timeoutMs = this.config.skillTimeoutMs) {
    const ctx = { aborted: false };
    let depth = 0;
    const hooks = {
      useSkill: async (name, a) => {
        if (!this.skills.has(name)) throw new Error(`no saved skill "${name}". Saved skills: ${Object.keys(this.skills.index).join(', ')}`);
        if (depth >= 3) throw new Error('skills nested too deeply (max 3)');
        depth++;
        try {
          return await runSkill(this.skills.code(name), api, a, ctx, this.config.skillTimeoutMs);
        } finally {
          depth--;
        }
      },
    };
    const logs = [];
    this.lastExecLogs = logs;
    this.lastExecErrors = ctx.errors = [];
    ctx.actions = 0;
    this.lastExecCtx = ctx;
    const api = createApi(
      this.bot,
      ctx,
      (m) => {
        logs.push(String(m));
        this.events.log(`  · ${m}`);
      },
      hooks,
    );
    // Small models keep writing api.collect_logs(...) for saved skills. Instead of
    // fighting that, make every saved skill callable as api.<skill_name>(args).
    for (const name of Object.keys(this.skills.index)) {
      if (name in api) continue;
      api[name] = (a = {}) => {
        ctx.actions++;
        const args = a && typeof a === 'object' ? a : { count: a };
        const p = hooks.useSkill(name, args);
        p.catch(() => {});
        return p;
      };
    }
    try {
      return await runSkill(code, api, args, ctx, timeoutMs);
    } finally {
      ctx.aborted = true;
      // NOT pathfinder.stop(): that only sets a flag, and if the bot is already standing
      // still the flag stays set and cancels the NEXT skill's first move ("Path was stopped").
      // setGoal(null) clears the path right away (and also clears a stale stop flag).
      this.bot.pathfinder.setGoal(null);
      this.bot.clearControlStates();
    }
  }

  /**
   * Self-improvement pass over accumulated failures:
   * 1) rewrite skills that keep failing (using their code + the error messages),
   * 2) distill the journal into a fresh, short lesson list.
   */
  async reflect() {
    this.reflections++;
    if (this.reflections % this.config.consolidateEvery === 0 || this.skills.learnedCount() > this.config.maxSkills) {
      this.consolidate();
    }
    const digest = this.journal.failureDigest(this.reflectedAt);
    this.reflectedAt = this.journal.entries.length;
    const repeated = Object.entries(digest.bySkill).filter(([, f]) => f.n >= 3);
    const failing = repeated.filter(([name]) => this.skills.has(name));
    if (!repeated.length && !digest.deaths.length) return;
    this.events.log(`🔁 반성 시간: 반복 실패 ${repeated.length}개(저장된 스킬 ${failing.length}개 수리), 죽음 ${digest.deaths.length}회 분석`);

    const obs = observe(this.bot);
    const goal = nextGoal(obs.inv, this.memory);
    for (const [name, f] of failing.slice(0, 2)) {
      const request =
        `스킬 "${name}"이(가) 최근 ${f.n}번 실패했다. 에러들: ${f.errors.join(' | ')}\n` +
        `현재 코드:\n${this.skills.code(name)}\n같은 이름(${name})으로, 이 에러들이 나지 않게 고쳐서 다시 작성해라.`;
      const result = await this.writeSkill(request, obs, goal);
      this.journal.add('reflect_fix', { name, result: short(result, 120) });
    }

    const text = await this.llm.ask(
      'smart',
      REFLECT_SYSTEM,
      `기존 교훈:\n${this.memory.data.lessons.map((l) => '- ' + l).join('\n') || '- 없음'}\n\n최근 일지:\n${this.journal.recentText(40)}\n\n${this.journal.deathSummary()}`,
      { maxTokens: 2000 },
    );
    const { lessons } = parseJson(text);
    if (Array.isArray(lessons) && lessons.length) {
      this.memory.data.lessons = lessons.filter((l) => typeof l === 'string').slice(0, 8);
      this.memory.save();
      this.journal.add('reflect', { lessons: this.memory.data.lessons.length });
      this.events.log(`🧩 교훈 ${this.memory.data.lessons.length}개로 정리`);
    }
  }

  /** Data-driven plan for the current goal + the concrete next skill call. */
  #planText(goal, inv) {
    const t = goal.target;
    if (!t) return '';
    try {
      if (t.item === 'log') {
        const have = Object.entries(inv).reduce((n, [k, v]) => (k.endsWith('_log') ? n + v : n), 0);
        const next = { skill: 'collect_logs', args: { count: Math.max(1, t.count - have) } };
        return `자동 계획(게임 데이터):\n1. 원목 캐기 x${next.args.count}\n추천 다음 행동: ${JSON.stringify(next)}`;
      }
      const reg = this.bot.registry;
      const near = (b) => !!this.bot.findBlock({ matching: reg.blocksByName[b].id, maxDistance: 32 });
      const steps = makePlan(reg, inv, t.item, t.count, { tableNearby: near('crafting_table'), furnaceNearby: near('furnace') });
      if (!steps.length) return '';
      const next = stepToAction(steps[0]);
      return (
        `자동 계획(게임 데이터, ${t.item} x${t.count}):\n` +
        steps.slice(0, 8).map((st, i) => `${i + 1}. ${stepText(st)}`).join('\n') +
        (steps.length > 8 ? `\n... (${steps.length - 8}단계 더)` : '') +
        (next ? `\n추천 다음 행동: ${JSON.stringify(next)}` : '')
      );
    } catch (e) {
      return `자동 계획 실패: ${e.message}`;
    }
  }

  /** Snapshot the brain, then archive useless/failing skills. */
  consolidate() {
    const snap = this.#snapshot('before-consolidate');
    const archived = this.skills.consolidate(this.config.maxSkills);
    this.journal.add('consolidate', { archived, snapshot: snap });
    this.events.update({ skills: this.skills.count() });
    this.events.log(
      archived.length
        ? `🧹 두뇌 정리: ${archived.length}개 보관함으로 (${archived.join(', ')})`
        : '🧹 두뇌 정리: 정리할 스킬 없음',
    );
    return archived;
  }

  async useSkill(name, args) {
    if (!this.skills.has(name)) {
      const alt = this.skills.closest(name);
      if (!alt) {
        this.events.log(`❓ 없는 스킬 "${name}"`);
        return `실패: "${name}" 스킬은 존재하지 않는다 (저장된 적 없음). 보유 스킬 목록에 있는 이름만 skill로 실행할 것`;
      }
      this.events.log(`🔀 없는 스킬 "${name}" → 비슷한 "${alt}" 실행`);
      name = alt;
    }
    try {
      const before = snapshotState(this.bot);
      const out = await this.#exec(this.skills.code(name), args);
      const d = diffState(before, snapshotState(this.bot));
      if (!d.effect) {
        // Finished without error but changed nothing: not a real success.
        this.skills.record(name, false);
        this.journal.add('skill_fail', { name, error: '효과 없음 (인벤토리/위치/허기/몹 변화 없음)' });
        this.events.log(`➖ ${name}: 효과 없음`);
        const sw = this.lastExecErrors ?? [];
        if (!sw.length && (this.lastExecCtx?.actions ?? 0) === 0)
          return `효과 없음: "${name}"은 아무 동작도 호출하지 않고 끝났다 (조건이 안 맞음). 실제 인벤토리: ${JSON.stringify(inventoryOf(this.bot))}. 이 상황엔 다른 스킬(예: craft_item {item:'oak_planks'})을 쓸 것`;
        return sw.length
          ? `효과 없음: "${name}" 안에서 실패한 호출: ${short(sw.join(' | '), 400)}`
          : `효과 없음: "${name}"이 에러 없이 끝났지만 아무것도 바뀌지 않았다. 다른 스킬이나 다른 장소를 시도하거나 write_skill로 고칠 것`;
      }
      this.skills.record(name, true);
      this.journal.add('skill_ok', { name, effect: d.text });
      const sw = this.lastExecErrors ?? [];
      this.events.log(`✅ ${name}: ${d.text}${sw.length ? ` (내부 실패 ${sw.length}건)` : ''}`);
      return `성공: ${d.text}${out !== undefined ? ` / 반환값 ${short(out, 200)}` : ''}${sw.length ? ` / 단, 내부에서 실패한 호출: ${short(sw.join(' | '), 300)}` : ''}`;
    } catch (e) {
      this.skills.record(name, false);
      this.journal.add('skill_fail', { name, error: short(e.message, 160) });
      this.events.log(`❌ ${name}: ${e.message}`);
      return `실패: ${e.message}`;
    }
  }

  /** Smart model writes code -> run -> on error feed it back -> save on success. */
  async writeSkill(request, obs, goal, maxAttempts = 3) {
    const messages = [
      {
        role: 'user',
        content: `요청: ${request}\n현재 목표: ${goal.text}\n${obs.text}\n\n기존 스킬:\n${this.skills.describe()}`,
      },
    ];
    let lastErr = '';
    for (let i = 1; i <= maxAttempts; i++) {
      const reply = await this.llm.ask('smart', WRITER_SYSTEM, messages, { maxTokens: 8000 });
      messages.push({ role: 'assistant', content: reply });
      let parsed;
      try {
        parsed = parseWriterReply(reply);
        if (!Skills.validName(parsed.name)) throw new Error(`잘못된 스킬 이름: ${parsed.name}`);
        this.events.log(`🛠️ 스킬 작성 시도 ${i}/${maxAttempts}: ${parsed.name}`);
        const before = snapshotState(this.bot);
        if (Skills.isBuiltin(parsed.name))
          throw new Error(`"${parsed.name}" is a builtin skill and cannot be overwritten - use a different NAME`);
        const out = await this.#exec(parsed.code, parsed.args, this.config.writeTestTimeoutMs);
        const d = diffState(before, snapshotState(this.bot));
        if (!d.effect) {
          const swallowed = this.lastExecErrors ?? [];
          const acted = this.lastExecCtx?.actions ?? 0;
          throw new Error(
            swallowed.length
              ? `NOTHING changed because these api calls failed (your code caught and ignored them): ${swallowed.join(' | ')}`
              : acted === 0
                ? `NOTHING changed: your code never called any action (gather/craft/place/goTo/...). An if-condition was probably false or it returned early. Do not guess the inventory - the REAL inventory right now is ${JSON.stringify(inventoryOf(this.bot))}. Call the actions directly; they throw a clear error if something is missing.`
                : 'code finished without errors but NOTHING changed (inventory, position, food, nearby mobs). Check that every api call is awaited and targets the right block/item.',
          );
        }
        this.skills.save(parsed.name, parsed.description, parsed.args, parsed.code);
        this.skills.record(parsed.name, true);
        this.journal.add('new_skill', { name: parsed.name });
        this.events.log(`🎓 새 스킬 습득: ${parsed.name}`);
        this.events.update({ skills: this.skills.count(), newSkill: parsed.name });
        return `새 스킬 "${parsed.name}" 작성·실행 성공: ${d.text}${out !== undefined ? ` / 반환값 ${short(out, 200)}` : ''}`;
      } catch (e) {
        lastErr = e.message;
        this.journal.add('skill_fail', { name: parsed?.name ?? `write:${short(request, 30)}`, error: short(lastErr, 160), writing: true });
        this.events.log(`❌ 시도 ${i} 실패: ${lastErr}`);
        if (parsed?.code) this.#keepFailed(parsed, lastErr);
        const logs = (this.lastExecLogs ?? []).slice(-6);
        messages.push({
          role: 'user',
          content:
            `실행 실패: ${lastErr}\n` +
            (logs.length ? `실행 중 api.log 출력:\n${logs.map((l) => '- ' + short(l, 160)).join('\n')}\n` : '실행 중 api.log 출력: 없음 (api 호출이 한 번도 실행되지 않았을 수 있음 - 조건문/return 확인)\n') +
            `테스트 인자: ${JSON.stringify(parsed?.args ?? {})}\n` +
            `현재 인벤토리: ${JSON.stringify(inventoryOf(this.bot))}\n원인을 고쳐서 같은 형식으로 다시 작성해.`,
        });
        this.lastExecLogs = [];
      }
    }
    this.memory.addLesson(`스킬 작성 실패 "${short(request, 80)}": ${lastErr}`);
    return `스킬 작성 ${maxAttempts}번 실패: ${lastErr}`;
  }
}
