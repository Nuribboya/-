// What the bot is working on right now: the fixed tool path (curriculum.js) first,
// then goals it picks for itself, plus the data-driven plan shown to the model.
import { parseJson } from './llm.js';
import { nextGoal } from './curriculum.js';
import { plan as makePlan, stepText, stepToAction } from './knowledge.js';
import { GOAL_SYSTEM } from './prompts.js';

// What a player usually goes for after the iron pickaxe, cheapest/most useful first.
// Used as suggestions and as the fallback when the model picks something already done.
const NEXT_GOALS = [
  { item: 'shield', count: 1, why: '스켈레톤 화살 막기' },
  { item: 'bucket', count: 1, why: '용암 끄기, 물 운반' },
  { item: 'iron_sword', count: 1, why: '전투' },
  { item: 'torch', count: 16, why: '밤과 동굴 밝히기' },
  { item: 'cooked_beef', count: 5, why: '좋은 음식' },
  { item: 'chest', count: 1, why: '귀중품 보관' },
  { item: 'iron_helmet', count: 1, why: '갑옷(철 5)' },
  { item: 'iron_boots', count: 1, why: '갑옷(철 4)' },
  { item: 'white_bed', count: 1, why: '부활 지점' },
  { item: 'iron_leggings', count: 1, why: '갑옷(철 7)' },
  { item: 'iron_chestplate', count: 1, why: '갑옷(철 8)' },
  { item: 'diamond', count: 3, why: '다이아 도구 재료' },
  { item: 'diamond_pickaxe', count: 1, why: '흑요석 채굴' },
  { item: 'diamond_sword', count: 1, why: '최고 무기' },
];

export class Goals {
  constructor({ bot, llm, memory, journal, events, config, snapshot = () => {}, advancements = null }) {
    Object.assign(this, { bot, llm, memory, journal, events, config, snapshot, advancements });
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
    const asGoal = (g) => ({ id: 'free', text: g.text, target: g.target, finish: g.finish ?? null, advId: g.advId ?? null });

    if (fg && !advance) return asGoal(fg);
    if (!fg && !advance) return { ...fallback, target: null };

    const owned = this.#owned(obs.inv);
    if (fg) {
      fg.steps = (fg.steps ?? 0) + 1;
      const haveTarget = fg.target && (owned[fg.target.item] ?? 0) >= fg.target.count;
      // Advancement goals are done when the server says so (an item-only one also when we have it).
      const done = fg.advId ? this.advancements?.isDone(fg.advId) || (haveTarget && !fg.finish) : haveTarget;
      if (!done && fg.steps <= this.config.freeGoalMaxSteps) {
        this.memory.save();
        return asGoal(fg);
      }
      m.goalHistory = [...m.goalHistory, { goal: fg.text, item: fg.target?.item ?? null, advId: fg.advId ?? null, result: done ? '달성' : '포기' }].slice(-10);
      m.freeGoal = null;
      this.journal.add(done ? 'self_goal_done' : 'self_goal_dropped', { goal: fg.text });
      this.events.log(done ? `🏁 스스로 정한 목표 달성: ${fg.text}` : `🏳️ 목표 포기 (${fg.steps}턴): ${fg.text}`);
      if (done) this.snapshot(`self-${fg.target?.item ?? 'goal'}`);
      else this.memory.addLesson(`"${fg.text}"은(는) ${fg.steps}턴 안에 못 했다 - 더 작게 나누거나 다른 방법`);
    }

    // Advancements first: deterministic, no model call. Skip ones given up on in the last 3 goals.
    if (this.config.advancementGoals !== false && this.advancements) {
      const skip = new Set(m.goalHistory.slice(-3).filter((g) => g.result === '포기').map((g) => g.advId).filter(Boolean));
      const adv = this.advancements.next(skip);
      if (adv) {
        m.freeGoal = { text: adv.text, target: adv.target, finish: adv.finish, advId: adv.id, steps: 0, setAt: new Date().toISOString() };
        this.memory.save();
        this.journal.add('self_goal', { goal: adv.text, target: adv.target, advancement: adv.title });
        this.events.log(`🏆 다음 목표 (발전 과제): ${adv.title}`);
        return asGoal(m.freeGoal);
      }
    }

    const reg = this.bot.registry;
    // Items we already have enough of, or went for in the last 5 goals, are not new goals.
    const recent = new Set(m.goalHistory.slice(-5).map((g) => g.item).filter(Boolean));
    const isFresh = (item, count) => !!reg.itemsByName[item] && (owned[item] ?? 0) < count && !recent.has(item);
    let candidates = NEXT_GOALS.filter((c) => isFresh(c.item, c.count));
    // Died to mobs without armor more than once: protection first.
    if (this.preferGear) {
      const gear = (c) => /_(helmet|chestplate|leggings|boots)$|^shield$/.test(c.item);
      candidates = [...candidates.filter(gear), ...candidates.filter((c) => !gear(c))];
    }

    const text = await this.llm.ask(
      'fast',
      GOAL_SYSTEM,
      [
        obs.text,
        this.journal.deathSummary() ? `죽음 기록: ${this.journal.deathSummary()}` : '',
        `최근 목표(다시 고르지 말 것): ${m.goalHistory.length ? m.goalHistory.map((g) => `${g.goal}(${g.result})`).join(', ') : '없음'}`,
        candidates.length ? `추천 후보(아직 없는 것): ${candidates.slice(0, 5).map((c) => `${c.item} x${c.count}(${c.why})`).join(', ')}` : '',
      ]
        .filter(Boolean)
        .join('\n'),
      { maxTokens: 800, localMaxTokens: 250 },
    );
    let g = {};
    try {
      g = parseJson(text);
    } catch {}
    let item = typeof g.item === 'string' && reg.itemsByName[g.item] ? g.item : null;
    let count = Math.max(1, Math.min(64, Number(g.count) || 1));
    let goalText = String(g.goal || '').slice(0, 120);
    // Already have it / just did it / nothing checkable: take the next sensible step instead,
    // otherwise the bot "achieves" the same goal over and over.
    if (candidates.length && (!item || !isFresh(item, count))) {
      const c = candidates[0];
      this.events.log(`🔁 "${goalText || g.item || '?'}"은(는) 이미 있거나 최근 목표 → ${c.item} x${c.count}`);
      item = c.item;
      count = c.count;
      goalText = `${c.item} x${c.count} 만들기 (${c.why})`;
    }
    m.freeGoal = {
      text: goalText || '주변을 탐험하며 쓸만한 자원 모으기',
      target: item && isFresh(item, count) ? { item, count } : null,
      steps: 0,
      setAt: new Date().toISOString(),
    };
    this.memory.save();
    this.journal.add('self_goal', { goal: m.freeGoal.text, target: m.freeGoal.target });
    const t = m.freeGoal.target;
    this.events.log(`🎯 스스로 정한 새 목표: ${m.freeGoal.text}${t ? ` (${t.item} x${t.count})` : ''}`);
    return asGoal(m.freeGoal);
  }

  /** Inventory plus what is worn (armor) or held in the off-hand (shield): equipping is not losing. */
  #owned(inv) {
    const owned = { ...inv };
    const slots = this.bot.inventory?.slots;
    if (slots) for (const i of [5, 6, 7, 8, 45]) {
      const it = slots[i];
      if (it) owned[it.name] = (owned[it.name] ?? 0) + it.count;
    }
    return owned;
  }

  /**
   * Plan for the goal from the current inventory: { steps: [{text, action}], label } or null.
   * One source for both the prompt text and follow_plan, so they always agree.
   */
  plan(goal, inv) {
    const t = goal?.target;
    // Got the item (or none needed): what is left is the finishing action (wear it, sleep, eat...).
    if (goal?.finish && (!t || (this.#owned(inv)[t.item] ?? 0) >= t.count)) {
      return { label: goal.finish.text, steps: [{ text: goal.finish.text, action: goal.finish.action }] };
    }
    if (!t) return null;
    if (t.item === 'log') {
      const have = Object.entries(inv).reduce((n, [k, v]) => (k.endsWith('_log') ? n + v : n), 0);
      if (have >= t.count) return { label: '원목', steps: [] };
      const n = t.count - have;
      return { label: '원목', steps: [{ text: `원목 캐기 x${n}`, action: { skill: 'collect_logs', args: { count: n } } }] };
    }
    const reg = this.bot.registry;
    const near = (b) => !!this.bot.findBlock({ matching: reg.blocksByName[b].id, maxDistance: 32 });
    const steps = makePlan(reg, inv, t.item, t.count, { tableNearby: near('crafting_table'), furnaceNearby: near('furnace') });
    return { label: `${t.item} x${t.count}`, steps: steps.map((st) => ({ text: stepText(st), action: stepToAction(st) })) };
  }

  /** The next concrete skill call of the plan (null if there is no plan, it is done, or unknown). */
  nextAction(goal, inv) {
    try {
      return this.plan(goal, inv)?.steps[0]?.action ?? null;
    } catch {
      return null;
    }
  }

  /** Data-driven plan for the goal + the concrete next skill call, as prompt text. */
  planText(goal, inv) {
    try {
      const p = this.plan(goal, inv);
      if (!p || !p.steps.length) return '';
      const next = p.steps[0].action;
      return (
        `자동 계획(게임 데이터, ${p.label}):\n` +
        p.steps.slice(0, 8).map((st, i) => `${i + 1}. ${st.text}`).join('\n') +
        (p.steps.length > 8 ? `\n... (${p.steps.length - 8}단계 더)` : '') +
        (next ? `\n추천 다음 행동: ${JSON.stringify(next)}` : '') +
        (next && p.steps.length > 1 ? `\n여러 단계를 한 번에(가장 빠름): {"skill":"follow_plan","args":{"steps":${Math.min(5, p.steps.length)}}}` : '')
      );
    } catch (e) {
      return `자동 계획 실패: ${e.message}`;
    }
  }
}
