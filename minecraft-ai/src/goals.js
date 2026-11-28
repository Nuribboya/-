// What the bot is working on right now: the fixed tool path (curriculum.js) first,
// then goals it picks for itself, plus the data-driven plan shown to the model.
import { parseJson } from './llm.js';
import { nextGoal } from './curriculum.js';
import { plan as makePlan, stepText, stepToAction } from './knowledge.js';
import { GOAL_SYSTEM } from './prompts.js';

export class Goals {
  constructor({ bot, llm, memory, journal, events, config, snapshot = () => {} }) {
    Object.assign(this, { bot, llm, memory, journal, events, config, snapshot });
  }

  /**
   * The current goal. `advance: true` means "a decision turn is spent on it" (counts toward
   * giving up a self-chosen goal, may pick a new one). Read-only callers pass advance: false.
   */
  async current(obs, { advance = true } = {}) {
    const before = new Set(this.memory.data.achieved);
    const goal = nextGoal(obs.inv, this.memory);
    for (const id of this.memory.data.achieved) {
      if (before.has(id)) continue;
      this.journal.add('milestone', { id });
      this.events.log(`🏁 목표 달성: ${id}`);
      this.snapshot(`ms-${id}`);
    }
    return goal.id === 'free' ? this.#freeGoal(obs, goal, advance) : goal;
  }

  /**
   * After the fixed tool path, the bot picks its own goals like a player would.
   * A goal with a target item is done when the inventory has it; any goal is dropped
   * after freeGoalMaxSteps turns so it can't get stuck forever.
   */
  async #freeGoal(obs, fallback, advance) {
    const m = this.memory.data;
    m.goalHistory ??= [];
    const fg = m.freeGoal;
    const asGoal = (g) => ({ id: 'free', text: g.text, target: g.target });

    if (fg && !advance) return asGoal(fg);
    if (!fg && !advance) return { ...fallback, target: null };

    if (fg) {
      fg.steps = (fg.steps ?? 0) + 1;
      const done = fg.target && (obs.inv[fg.target.item] ?? 0) >= fg.target.count;
      if (!done && fg.steps <= this.config.freeGoalMaxSteps) {
        this.memory.save();
        return asGoal(fg);
      }
      m.goalHistory = [...m.goalHistory, { goal: fg.text, result: done ? '달성' : '포기' }].slice(-10);
      m.freeGoal = null;
      this.journal.add(done ? 'self_goal_done' : 'self_goal_dropped', { goal: fg.text });
      this.events.log(done ? `🏁 스스로 정한 목표 달성: ${fg.text}` : `🏳️ 목표 포기 (${fg.steps}턴): ${fg.text}`);
      if (done) this.snapshot(`self-${fg.target.item}`);
      else this.memory.addLesson(`"${fg.text}"은(는) ${fg.steps}턴 안에 못 했다 - 더 작게 나누거나 다른 방법`);
    }

    const reg = this.bot.registry;
    const text = await this.llm.ask(
      'fast',
      GOAL_SYSTEM,
      [
        obs.text,
        this.journal.deathSummary() ? `죽음 기록: ${this.journal.deathSummary()}` : '',
        `최근 목표: ${m.goalHistory.length ? m.goalHistory.map((g) => `${g.goal}(${g.result})`).join(', ') : '없음'}`,
      ].join('\n'),
      { maxTokens: 800 },
    );
    let g = {};
    try {
      g = parseJson(text);
    } catch {}
    const item = typeof g.item === 'string' && reg.itemsByName[g.item] ? g.item : null;
    m.freeGoal = {
      text: String(g.goal || '주변을 탐험하며 쓸만한 자원 모으기').slice(0, 120),
      target: item ? { item, count: Math.max(1, Math.min(64, Number(g.count) || 1)) } : null,
      steps: 0,
      setAt: new Date().toISOString(),
    };
    this.memory.save();
    this.journal.add('self_goal', { goal: m.freeGoal.text, target: m.freeGoal.target });
    this.events.log(`🎯 스스로 정한 새 목표: ${m.freeGoal.text}${item ? ` (${item} x${m.freeGoal.target.count})` : ''}`);
    return asGoal(m.freeGoal);
  }

  /** Data-driven plan for the goal + the concrete next skill call, as prompt text. */
  planText(goal, inv) {
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
}
