import fs from 'node:fs';
import path from 'node:path';
import { parseJson } from './llm.js';
import { observe, inventoryOf, snapshotState, diffState, HOSTILE } from './observe.js';
import { createApi } from './actions.js';
import { runSkill } from './sandbox.js';
import { Skills } from './skills.js';
import { Journal } from './journal.js';
import { Brains } from './brain.js';
import { Goals } from './goals.js';
import { Advancements } from './advancements.js';
import { analyzeDeaths, classifyDeath, patternsText } from './deaths.js';
import { knowledgeText } from './knowledge.js';
import { wikiFor } from './wiki.js';
import { DECISION_SYSTEM, WRITER_SYSTEM, REFLECT_SYSTEM, parseWriterReply } from './prompts.js';
import { sleep, short, isNight, withTimeout } from './util.js';
import pf from 'mineflayer-pathfinder';

/**
 * Why a run that finished without throwing still changed nothing.
 * One explanation used for both saved skills and freshly written code.
 */
function noEffectMessage(run, inv) {
  if (run.errors.length)
    return `NOTHING changed because these api calls failed (the code caught and ignored them): ${run.errors.join(' | ')}`;
  if (run.actions === 0)
    return `NOTHING changed: the code never called any action (gather/craft/place/goTo/...). An if-condition was probably false or it returned early. Do not guess the inventory - the REAL inventory right now is ${JSON.stringify(inv)}. Call the actions directly; they throw a clear error if something is missing.`;
  return 'code finished without errors but NOTHING changed (inventory, position, food, nearby mobs). Check that every api call is awaited and targets the right block/item.';
}

/** The decision loop: observe -> ask the model -> run or write a skill -> learn from the result. */
export class Agent {
  constructor({ bot, llm, skills, memory, events, config, journal }) {
    Object.assign(this, { bot, llm, skills, memory, events, config });
    this.journal = journal ?? new Journal(config.dataDir);
    this.brains = new Brains(config.dataDir, config.brainsDir);
    this.advancements = new Advancements(memory.data.advancementsFromChat ?? []);
    this.goals = new Goals({ bot, llm, memory, journal: this.journal, events, config, snapshot: (label) => this.#snapshot(label), advancements: this.advancements });
    this.lastSelfMsg = { text: '', at: 0 };
    this.steps = 0;
    this.writeFailStreak = 0;
    this.writeCooldown = 0;
    this.reflections = 0;
    this.lastSnapshotAt = Date.now();
    this.reflectedAt = this.journal.entries.length;
    this.lastHealth = bot.health ?? 20;
    this.running = false;
    this.lastResult = '아직 없음';
    this.lastAction = '';
    this.deathAnalysis = analyzeDeaths(this.journal.of('death'));
    this.#applyPolicy();
    this.#listen();
  }

  get policy() {
    return this.deathAnalysis.policy;
  }

  /** Push the death-pattern policy into the pathfinder and goals. */
  #applyPolicy() {
    const p = this.policy;
    if (this.bot.__movements) this.bot.__movements.maxDropDown = p.maxDropDown;
    this.goals.preferGear = p.gearFirst;
  }

  /** Re-analyze all deaths; announce patterns that are new and apply the policy. */
  #reanalyzeDeaths() {
    const before = new Set(this.deathAnalysis.patterns.map((p) => p.key));
    this.deathAnalysis = analyzeDeaths(this.journal.of('death'));
    for (const p of this.deathAnalysis.patterns) {
      if (before.has(p.key)) continue;
      this.events.log(`🧠 죽음 패턴 발견: ${p.text} → ${p.change}`);
      this.journal.add('death_pattern', { key: p.key, text: p.text, change: p.change });
      this.memory.addLesson(`${p.text} → ${p.change}`);
    }
    this.#applyPolicy();
  }

  /** Is there solid ground above (cave/tunnel/inside) rather than open sky? */
  #underground() {
    const me = this.bot.entity?.position?.floored?.();
    if (!me || !this.bot.blockAt) return false;
    for (let d = 2; d < 18; d++) {
      const b = this.bot.blockAt(me.offset(0, d, 0));
      if (b && b.boundingBox !== 'empty') return true;
    }
    return false;
  }

  // ---------- world events -> journal ----------

  #listen() {
    const bot = this.bot;
    // Death message from the server ("<name> was slain by Zombie") tells us the cause.
    bot.on('messagestr', (msg) => {
      if (this.advancements.onChat(msg, bot.username)) return;
      if (msg.startsWith(bot.username)) this.lastSelfMsg = { text: msg, at: Date.now() };
    });

    // Running out of air (flooded tunnel, deep water): drop everything and swim up.
    // The reported air value is not reliable on every version (26.1 sends -1/0 on dry land),
    // so look at the world instead: is the head really in water, and for how long?
    this.underwaterSince = null;
    this.drownWatch = setInterval(() => {
      if (!this.#headInWater()) {
        this.underwaterSince = null;
        return;
      }
      this.underwaterSince ??= Date.now();
      const under = Date.now() - this.underwaterSince;
      const air = bot.oxygenLevel;
      const airLow = typeof air === 'number' && air >= 0 && air <= 20 && air <= 8;
      const cooledDown = Date.now() - (this.lastEscapeAt ?? 0) > 10000; // one escape at a time, not every second
      if (!this.escapingWater && cooledDown && (under >= (this.config.drownAfterMs ?? 7000) || (airLow && under >= 2000))) this.#escapeWater();
    }, Math.min(1000, Math.max(50, (this.config.drownAfterMs ?? 7000) / 5)));
    this.drownWatch.unref?.();

    // Advancement progress: the server sends all of it on join, then updates.
    bot._client?.on?.('advancements', (packet) => {
      try {
        this.advancements.onPacket(packet);
        this.events.update({ advancements: this.advancements.doneIds().length });
      } catch (e) {
        this.events.log(`⚠️ 발전 과제 데이터 읽기 실패: ${e.message}`);
      }
    });
    this.advancements.onDone((id, title) => {
      this.journal.add('advancement', { id, title });
      this.events.log(`🏆 발전 과제 달성: ${title}`);
      this.memory.data.advancementsFromChat = [...this.advancements.fromChat];
      this.memory.save();
      this.events.update({ advancements: this.advancements.doneIds().length });
    });

    // Damage log: big hits only, with the nearest mob as the likely source.
    bot.on('health', () => {
      const drop = this.lastHealth - bot.health;
      this.lastHealth = bot.health;
      if (drop >= 3 && bot.health > 0) {
        this.journal.add('damage', { amount: Math.round(drop), source: this.#nearestThreat(), where: this.#where() });
      }
    });

    bot.on('death', () => {
      const night = isNight(bot);
      const where = this.#where();
      const y = Math.floor(bot.entity?.position?.y ?? 0);
      const underground = this.#underground();
      const slots = bot.inventory?.slots ?? [];
      const armor = [5, 6, 7, 8].filter((i) => slots[i]).length;
      const weapon = Object.keys(this.lastInv ?? {}).some((n) => /_(sword|axe)$/.test(n));
      const p = bot.entity?.position;
      // Remember where and with what we died, to go back for the items after respawning.
      if (p && Object.keys(this.lastInv ?? {}).length)
        this.pendingRecovery = { x: p.x, y: p.y, z: p.z, at: Date.now(), cause: '', items: this.lastInv };
      const lastAction = this.lastAction;
      const lastResult = short(this.lastResult, 120);
      this.memory.addDeath();
      this.events.update({ deaths: this.memory.data.deaths });
      // The chat line with the cause can arrive just after the death event.
      setTimeout(() => {
        const recent = Date.now() - this.lastSelfMsg.at < 6000;
        const cause = recent ? this.lastSelfMsg.text.slice(bot.username.length + 1) : this.#nearestThreat() ?? '원인불명';
        if (this.pendingRecovery) this.pendingRecovery.cause = cause;
        this.journal.add('death', { cause, kind: classifyDeath(cause), where, y, underground, night, armor, weapon, lastAction, lastResult });
        this.#reanalyzeDeaths();
        this.memory.addLesson(`죽음 #${this.memory.data.deaths}: ${cause} (${night ? '밤' : '낮'}, ${where}), 직전 행동 "${lastAction}"`);
        this.events.log(`💀 죽었다: ${cause}`);
      }, 1000);
    });
  }

  async #escapeWater() {
    const bot = this.bot;
    this.escapingWater = true;
    this.lastEscapeAt = Date.now();
    const secs = this.underwaterSince ? Math.round((Date.now() - this.underwaterSince) / 1000) : 0;
    this.underwaterSince = null; // count again from zero after this escape
    this.events.log(`🫧 머리가 물속에 ${secs}초 - 하던 일 멈추고 물 밖으로`);
    this.journal.add('danger', { what: 'drowning', where: this.#where() });
    if (this.currentCtx) this.currentCtx.aborted = true; // stop the running skill
    try {
      bot.pathfinder?.setGoal?.(null);
      bot.setControlState?.('jump', true); // swim up
      await sleep(1500);
      const y = Math.floor(bot.entity?.position?.y ?? 0);
      const target = Math.max(63, y + 6);
      await withTimeout(bot.pathfinder.goto(new pf.goals.GoalY(target)), 12000, 'escape timeout').catch(() => {});
    } catch {}
    finally {
      bot.setControlState?.('jump', false);
      this.escapingWater = false;
    }
  }

  /**
   * Stuck detector for a running skill: the pathfinder is trying to walk but the bot has not
   * moved a block in stuckMs (digging does not count as stuck). Then stop the skill right away
   * instead of waiting minutes for its timeout, and run unstuck on the next turn.
   */
  #watchStuck(ctx) {
    const limit = this.config.stuckMs ?? 15000;
    let last = this.bot.entity?.position?.clone?.();
    let lastMove = Date.now();
    return setInterval(() => {
      const p = this.bot.entity?.position;
      if (!p || !last) return;
      if (p.distanceTo(last) > 1 || this.bot.targetDigBlock || !this.bot.pathfinder?.isMoving?.()) {
        if (p.distanceTo(last) > 1) last = p.clone();
        lastMove = Date.now();
        return;
      }
      if (Date.now() - lastMove < limit || ctx.aborted) return;
      ctx.abortReason = `stuck: walking but not moving for ${Math.round(limit / 1000)}s (wall/corner) - unstuck runs next`;
      ctx.aborted = true;
      this.stuck = { at: p.clone(), time: Date.now() };
      this.events.log(`🧱 끼임 감지 (${Math.round(p.x)},${Math.round(p.y)},${Math.round(p.z)}) - 하던 일 멈춤`);
      this.bot.pathfinder?.setGoal?.(null); // make the pending walk give up now
    }, Math.max(100, Math.min(1000, limit / 3)));
  }

  #where() {
    const p = this.bot.entity?.position;
    return p ? `${p.x.toFixed(0)},${p.y.toFixed(0)},${p.z.toFixed(0)}` : '?';
  }

  #nearestThreat() {
    const me = this.bot.entity;
    if (!me) return undefined;
    const e = this.bot.nearestEntity((x) => x.name && x.position.distanceTo(me.position) < 6 && x !== me && x.type !== 'object' && x.type !== 'orb');
    return e?.name;
  }

  // ---------- main loop ----------

  stop() {
    this.running = false;
    clearInterval(this.drownWatch);
  }

  /**
   * Is the bot really under water (cannot breathe)? The eye cell must be water AND the cell above
   * it must not be open air: a bot swimming at the surface has its eyes above the water line
   * even though its eye cell counts as water (water blocks are not full height).
   */
  #headInWater() {
    const p = this.bot.entity?.position;
    if (!p || !this.bot.blockAt) return false;
    const wet = (b) => !!b && (/water|bubble_column|kelp|seagrass/.test(b.name) || !!b.getProperties?.()?.waterlogged);
    const eye = p.offset(0, 1.62, 0).floored();
    if (!wet(this.bot.blockAt(eye))) return false;
    const above = this.bot.blockAt(eye.offset(0, 1, 0));
    const surface = above && above.boundingBox === 'empty' && !wet(above); // air right above: at the surface
    return !surface;
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

  /**
   * Autopilot: the next plan step when it is safe to skip the model this turn, else null.
   * Ask the model instead after a failure/no-effect, in danger, without a plan, or every
   * autopilotMaxStreak turns (so it keeps an eye on things and the overlay keeps "thinking").
   */
  #autopilotAction(goal, obs) {
    if (this.config.autopilot === false) return null;
    if ((this.autoStreak ?? 0) >= (this.config.autopilotMaxStreak ?? 5)) return null;
    const lastOk = this.lastResult === '아직 없음' || /^(성공|새 스킬)/.test(this.lastResult);
    if (!lastOk) return null;
    const bot = this.bot;
    const pol = this.policy;
    if ((bot.health ?? 20) <= 8 || (bot.food ?? 20) <= pol.foodMin) return null;
    const me = bot.entity?.position;
    const danger =
      me &&
      Object.values(bot.entities ?? {}).some(
        (e) => HOSTILE.has(e.name) && e.position.distanceTo(me) < (e.name === 'creeper' ? pol.creeperRadius : pol.hostileRadius),
      );
    if (danger) return null;
    if (pol.nightSurfaceDanger && isNight(bot) && !this.#underground()) return null;
    let p;
    try {
      p = this.goals.plan(goal, obs.inv);
    } catch {
      return null;
    }
    const first = p?.steps?.[0]?.action;
    if (!first || !this.skills.has(first.skill)) return null;
    return p.steps.length > 1 ? { skill: 'follow_plan', args: { steps: Math.min(5, p.steps.length) } } : first;
  }

  /**
   * Right after dying: go back for the items (no model call). Only once per death, only while
   * the items still exist (~5 min), and not when they are gone anyway (lava, void).
   */
  async #recoverAfterDeath() {
    const r = this.pendingRecovery;
    if (!r || this.bot.isAlive === false) return false;
    this.pendingRecovery = null;
    const age = Date.now() - r.at;
    if (age > 4.5 * 60000) return false;
    if (/lava|burn|fell out of the world|void|withered/i.test(r.cause)) {
      this.events.log(`💼 아이템 회수 안 함: ${r.cause} (아이템이 이미 사라짐)`);
      return false;
    }
    const thought = `💼 죽은 자리로 아이템 회수하러 감 (${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.z)})`;
    this.events.update({ thought });
    this.events.log(thought);
    this.lastAction = `recover_items(${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.z)})`;
    this.lastResult = await this.useSkill('recover_items', { x: r.x, y: r.y, z: r.z });
    this.journal.add('recover', { where: `${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.z)}`, result: short(this.lastResult, 120) });
    return true;
  }

  async step() {
    const obs = observe(this.bot);
    this.lastInv = obs.inv;
    if (await this.#recoverAfterDeath()) return;
    if (this.stuck) {
      this.stuck = null;
      this.events.update({ thought: '🧱 끼임 - 빠져나오는 중' });
      this.lastAction = 'unstuck()';
      this.lastResult = await this.useSkill('unstuck', {});
      this.journal.add('danger', { what: 'stuck', where: this.#where() });
      return;
    }
    const goal = await this.goals.current(obs);
    this.currentGoal = goal;
    const lessons = this.memory.data.lessons;

    const auto = this.#autopilotAction(goal, obs);
    if (auto) {
      this.autoStreak = (this.autoStreak ?? 0) + 1;
      const thought = `📋 계획대로 진행: ${auto.skill}${auto.skill === 'follow_plan' ? ` (${auto.args.steps}단계)` : ''}`;
      this.events.update({ thought, goal: goal.text });
      this.events.log(thought);
      this.lastAction = `${auto.skill}(${short(auto.args, 80)})`;
      this.lastResult = await this.useSkill(auto.skill, auto.args);
      this.events.update({ lastAction: this.lastAction });
      return;
    }
    this.autoStreak = 0;

    const planText = this.goals.planText(goal, obs.inv);
    const recent = this.journal.recentText(8);
    const wiki = wikiFor(
      [goal.text, goal.target?.item, obs.text, this.lastResult, planText, recent, (this.bot.food ?? 20) <= 12 ? '배고픔' : ''].join('\n'),
      this.bot.registry,
    );
    const user = [
      `현재 목표: ${goal.text}`,
      obs.text,
      `직전 행동: ${this.lastAction || '없음'}`,
      `직전 결과: ${short(this.lastResult)}`,
      planText,
      wiki,
      `보유 스킬:\n${this.skills.describe()}`,
      this.writeCooldown > 0
        ? `⛔ 스킬 작성이 연속 실패해서 앞으로 ${this.writeCooldown}턴 동안 write_skill 금지. 반드시 보유 스킬 중 하나를 "skill"로 실행할 것 (예: collect_logs, craft_item, place_block, explore_area).`
        : '',
      `최근 일지:\n${recent || '- 없음'}`,
      this.journal.deathSummary() ? `누적: ${this.journal.deathSummary()}` : '',
      patternsText(this.deathAnalysis),
      `교훈:\n${lessons.length ? lessons.map((l) => '- ' + l).join('\n') : '- 아직 없음'}`,
    ].join('\n\n');

    this.system ??= `${DECISION_SYSTEM}\n\n${knowledgeText(this.bot.registry)}`;
    const reply = await this.llm.ask('fast', this.system, user, { maxTokens: 1500, localMaxTokens: 450 });
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

  // ---------- running skill code ----------

  /**
   * Run skill code once and report everything about it. Never throws:
   * { ok, out, error, logs, errors (api calls that failed, even if caught), actions, diff }
   */
  async #run(code, args, timeoutMs = this.config.skillTimeoutMs) {
    const ctx = { aborted: false, errors: [], actions: 0 };
    this.currentCtx = ctx;
    const logs = [];
    let depth = 0;
    const hooks = {
      torches: this.config.torches,
      descendScan: this.policy.descendScan,
      planNext: () => this.goals.nextAction(this.currentGoal, inventoryOf(this.bot)),
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
        const p = hooks.useSkill(name, a && typeof a === 'object' ? a : { count: a });
        p.catch(() => {});
        return p;
      };
    }

    const before = snapshotState(this.bot);
    const result = { ok: true, out: undefined, error: null, logs, errors: ctx.errors };
    const watchdog = this.#watchStuck(ctx);
    try {
      result.out = await runSkill(code, api, args, ctx, timeoutMs);
    } catch (e) {
      result.ok = false;
      result.error = e;
    } finally {
      clearInterval(watchdog);
      ctx.aborted = true;
      if (this.currentCtx === ctx) this.currentCtx = null;
      // NOT pathfinder.stop(): that only sets a flag, and if the bot is already standing
      // still the flag stays set and cancels the NEXT skill's first move ("Path was stopped").
      // setGoal(null) clears the path right away (and also clears a stale stop flag).
      this.bot.pathfinder.setGoal(null);
      this.bot.clearControlStates();
    }
    result.actions = ctx.actions;
    result.diff = diffState(before, snapshotState(this.bot));
    return result;
  }

  /** Record a failed run; a learned skill that failed 5 times in a row is archived on the spot. */
  #recordFail(name, error) {
    this.skills.record(name, false);
    this.journal.add('skill_fail', { name, error });
    const s = this.skills.index[name];
    if (s && !s.builtin && s.streak >= 5 && this.skills.archive(name)) {
      this.journal.add('consolidate', { archived: [name], reason: 'streak' });
      this.events.log(`🧹 "${name}" 5번 연속 실패 → 보관함으로 (기본 스킬을 쓰도록)`);
      this.events.update({ skills: this.skills.count() });
    }
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

    const r = await this.#run(this.skills.code(name), args, this.config.skillTimeoutMs * (this.skills.index[name]?.timeoutMul ?? 1));
    if (!r.ok) {
      this.events.log(`❌ ${name}: ${r.error.message}`);
      this.#recordFail(name, short(r.error.message, 160));
      return `실패: ${r.error.message}`;
    }
    if (!r.diff.effect && !this.skills.index[name]?.passive) {
      // Finished without error but changed nothing: not a real success.
      this.events.log(`➖ ${name}: 효과 없음`);
      this.#recordFail(name, '효과 없음 (인벤토리/위치/허기/몹 변화 없음)');
      return `효과 없음: "${name}" - ${short(noEffectMessage(r, inventoryOf(this.bot)), 500)} 이 상황엔 다른 스킬을 쓰거나 write_skill로 고칠 것`;
    }
    this.skills.record(name, true);
    this.journal.add('skill_ok', { name, effect: r.diff.text });
    this.events.log(`✅ ${name}: ${r.diff.text}${r.errors.length ? ` (내부 실패 ${r.errors.length}건)` : ''}`);
    return (
      `성공: ${r.diff.text}` +
      (r.out !== undefined ? ` / 반환값 ${short(r.out, 200)}` : '') +
      (r.errors.length ? ` / 단, 내부에서 실패한 호출: ${short(r.errors.join(' | '), 300)}` : '')
    );
  }

  /** Smart model writes code -> run -> on error feed it back -> save on success. */
  async writeSkill(request, obs, goal, maxAttempts = 3) {
    const messages = [
      {
        role: 'user',
        content: [
          `요청: ${request}`,
          `현재 목표: ${goal.text}`,
          obs.text,
          wikiFor(`${request}\n${goal.text}\n${goal.target?.item ?? ''}`, this.bot.registry, { max: 2 }),
          `기존 스킬:\n${this.skills.describe()}`,
        ]
          .filter(Boolean)
          .join('\n\n'),
      },
    ];
    let lastErr = '';
    for (let i = 1; i <= maxAttempts; i++) {
      const reply = await this.llm.ask('smart', WRITER_SYSTEM, messages, { maxTokens: 8000, localMaxTokens: 1500 });
      messages.push({ role: 'assistant', content: reply });
      let parsed = null;
      let r = null; // only this attempt's run - never an older one
      try {
        parsed = parseWriterReply(reply);
        if (!Skills.validName(parsed.name)) throw new Error(`잘못된 스킬 이름: ${parsed.name}`);
        if (Skills.isBuiltin(parsed.name))
          throw new Error(`"${parsed.name}" is a builtin skill and cannot be overwritten - use a different NAME`);
        const pointless = Skills.pointlessName(parsed.name);
        if (pointless) {
          // Don't spend the remaining attempts on it: tell the decision model and stop here.
          this.events.log(`🚫 작성 안 함: ${parsed.name}`);
          this.journal.add('skill_fail', { name: parsed.name, error: short(pointless, 160), writing: true });
          return `스킬 작성 안 함: ${pointless}`;
        }
        this.events.log(`🛠️ 스킬 작성 시도 ${i}/${maxAttempts}: ${parsed.name}`);
        r = await this.#run(parsed.code, parsed.args, this.config.writeTestTimeoutMs);
        if (!r.ok) throw r.error;
        if (!r.diff.effect) throw new Error(noEffectMessage(r, inventoryOf(this.bot)));

        this.skills.save(parsed.name, parsed.description, parsed.args, parsed.code);
        this.skills.record(parsed.name, true);
        this.journal.add('new_skill', { name: parsed.name });
        this.events.log(`🎓 새 스킬 습득: ${parsed.name}`);
        this.events.update({ skills: this.skills.count(), newSkill: parsed.name });
        return `새 스킬 "${parsed.name}" 작성·실행 성공: ${r.diff.text}${r.out !== undefined ? ` / 반환값 ${short(r.out, 200)}` : ''}`;
      } catch (e) {
        lastErr = e.message;
        this.journal.add('skill_fail', { name: parsed?.name ?? `write:${short(request, 30)}`, error: short(lastErr, 160), writing: true });
        this.events.log(`❌ 시도 ${i} 실패: ${lastErr}`);
        if (parsed?.code) this.#keepFailed(parsed, lastErr);
        const logs = (r?.logs ?? []).slice(-6);
        messages.push({
          role: 'user',
          content:
            `실행 실패: ${lastErr}\n` +
            (!r
              ? '코드를 실행하기 전에 실패했다 (출력 형식이나 이름 문제).\n'
              : logs.length
                ? `실행 중 api.log 출력:\n${logs.map((l) => '- ' + short(l, 160)).join('\n')}\n`
                : '실행 중 api.log 출력: 없음 (api 호출이 한 번도 실행되지 않았을 수 있음 - 조건문/return 확인)\n') +
            `테스트 인자: ${JSON.stringify(parsed?.args ?? {})}\n` +
            `현재 인벤토리: ${JSON.stringify(inventoryOf(this.bot))}\n원인을 고쳐서 같은 형식으로 다시 작성해.`,
        });
      }
    }
    this.memory.addLesson(`스킬 작성 실패 "${short(request, 80)}": ${lastErr}`);
    return `스킬 작성 ${maxAttempts}번 실패: ${lastErr}`;
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

  // ---------- self-improvement ----------

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
    // Read-only: reflecting must not spend a turn of the current goal or pick a new one.
    const goal = await this.goals.current(obs, { advance: false });
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
      { maxTokens: 2000, localMaxTokens: 1200, json: true },
    );
    let lessons = null;
    try {
      ({ lessons } = parseJson(text));
    } catch {
      this.events.log('⚠️ 교훈 정리 답변이 JSON이 아니어서 이번엔 건너뜀');
      return;
    }
    if (Array.isArray(lessons) && lessons.length) {
      this.memory.data.lessons = lessons.filter((l) => typeof l === 'string').slice(0, 8);
      this.memory.save();
      this.journal.add('reflect', { lessons: this.memory.data.lessons.length });
      this.events.log(`🧩 교훈 ${this.memory.data.lessons.length}개로 정리`);
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
}
