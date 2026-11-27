import { parseJson } from './llm.js';
import { observe, inventoryOf } from './observe.js';
import { nextGoal } from './curriculum.js';
import { createApi } from './actions.js';
import { runSkill } from './sandbox.js';
import { Skills } from './skills.js';
import { Journal } from './journal.js';
import { DECISION_SYSTEM, WRITER_SYSTEM, parseWriterReply } from './prompts.js';

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
      }
    }
    const lessons = this.memory.data.lessons;

    const user = [
      `현재 목표: ${goal.text}`,
      obs.text,
      `직전 행동: ${this.lastAction || '없음'}`,
      `직전 결과: ${short(this.lastResult)}`,
      `보유 스킬:\n${this.skills.describe()}`,
      `최근 일지:\n${this.journal.recentText(8) || '- 없음'}`,
      this.journal.deathSummary() ? `누적: ${this.journal.deathSummary()}` : '',
      `교훈:\n${lessons.length ? lessons.map((l) => '- ' + l).join('\n') : '- 아직 없음'}`,
    ].join('\n\n');

    const reply = await this.llm.ask('fast', DECISION_SYSTEM, user, { maxTokens: 1500 });
    const d = parseJson(reply);
    this.events.update({ thought: d.thought ?? '', goal: goal.text });
    this.events.log(`🧠 ${d.thought ?? ''}`);

    if (d.write_skill) {
      this.lastAction = `write_skill: ${short(d.write_skill, 80)}`;
      this.lastResult = await this.writeSkill(d.write_skill, obs, goal);
    } else if (d.skill) {
      this.lastAction = `${d.skill}(${short(d.args ?? {}, 80)})`;
      this.lastResult = await this.useSkill(d.skill, d.args ?? {});
    } else {
      this.lastAction = 'wait';
      this.lastResult = '대기함';
    }
    this.events.update({ lastAction: this.lastAction });
  }

  async #exec(code, args) {
    const ctx = { aborted: false };
    const api = createApi(this.bot, ctx, (m) => this.events.log(`  · ${m}`));
    try {
      return await runSkill(code, api, args, ctx, this.config.skillTimeoutMs);
    } finally {
      ctx.aborted = true;
      this.bot.pathfinder.stop();
      this.bot.clearControlStates();
    }
  }

  async useSkill(name, args) {
    if (!this.skills.has(name)) return `실패: "${name}" 스킬이 없다. 있는 스킬만 쓰거나 write_skill 할 것`;
    try {
      const out = await this.#exec(this.skills.code(name), args);
      this.skills.record(name, true);
      this.journal.add('skill_ok', { name });
      this.events.log(`✅ ${name}`);
      return `성공: ${short(out ?? 'ok')}`;
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
        const out = await this.#exec(parsed.code, parsed.args);
        this.skills.save(parsed.name, parsed.description, parsed.args, parsed.code);
        this.skills.record(parsed.name, true);
        this.journal.add('new_skill', { name: parsed.name });
        this.events.log(`🎓 새 스킬 습득: ${parsed.name}`);
        this.events.update({ skills: this.skills.count(), newSkill: parsed.name });
        return `새 스킬 "${parsed.name}" 작성·실행 성공: ${short(out ?? 'ok')}`;
      } catch (e) {
        lastErr = e.message;
        this.events.log(`❌ 시도 ${i} 실패: ${lastErr}`);
        messages.push({
          role: 'user',
          content: `실행 실패: ${lastErr}\n현재 인벤토리: ${JSON.stringify(inventoryOf(this.bot))}\n원인을 고쳐서 같은 형식으로 다시 작성해.`,
        });
      }
    }
    this.memory.addLesson(`스킬 작성 실패 "${short(request, 80)}": ${lastErr}`);
    return `스킬 작성 ${maxAttempts}번 실패: ${lastErr}`;
  }
}
