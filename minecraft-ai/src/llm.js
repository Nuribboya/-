import Anthropic from '@anthropic-ai/sdk';

// $ per 1M tokens [input, output]. Used only for the daily budget guard (estimate).
const PRICES = {
  'claude-haiku-5-5': [0.1, 0.5],
  'claude-sonnet-5-5': [2, 10],
  'claude-opus-5-5': [4, 20],
};

const DAY_MS = 24 * 60 * 60 * 1000;

export class LLM {
  constructor(config) {
    this.config = config;
    this._anthropic = null;
    this.spentUsd = 0;
    this.dayStart = Date.now();
  }

  get anthropic() {
    return (this._anthropic ??= new Anthropic());
  }

  budgetLeft() {
    if (Date.now() - this.dayStart > DAY_MS) {
      this.dayStart = Date.now();
      this.spentUsd = 0;
    }
    return this.spentUsd < this.config.dailyBudgetUsd;
  }

  /**
   * @param {'fast'|'smart'} tier
   * @param {string} system  stable instructions (cached on Anthropic)
   * @param {string|Array<{role:string,content:string}>} messages
   */
  async ask(tier, system, messages, { maxTokens = 1500 } = {}) {
    const { provider, model } = this.config[tier];
    const msgs = typeof messages === 'string' ? [{ role: 'user', content: messages }] : messages;
    if (provider === 'ollama') return this.#ollama(model, system, msgs, tier === 'fast');
    if (provider === 'anthropic') return this.#anthropicCall(tier, model, system, msgs, maxTokens);
    throw new Error(`unknown provider: ${provider}`);
  }

  async #anthropicCall(tier, model, system, messages, maxTokens) {
    const params = {
      model,
      max_tokens: maxTokens,
      system: [{ type: 'text', text: system, cache_control: { type: 'ephemeral' } }],
      messages,
      output_config: { effort: tier === 'fast' ? 'low' : 'medium' },
    };

    let res;
    if (model === 'claude-sonnet-5-5') {
      // Opt in to server-side refusal fallback; if the API rejects it, retry plain.
      try {
        res = await this.anthropic.beta.messages.create({
          ...params,
          betas: ['server-side-fallback-2026-07-01'],
          fallbacks: 'default',
        });
      } catch (e) {
        if (e?.status !== 400) throw e;
        res = await this.anthropic.messages.create(params);
      }
    } else {
      res = await this.anthropic.messages.create(params);
    }

    this.#charge(model, res.usage);
    if (res.stop_reason === 'refusal') throw new Error('model refused this request');
    return res.content.filter((b) => b.type === 'text').map((b) => b.text).join('');
  }

  #charge(model, usage = {}) {
    const [pin, pout] = PRICES[model] ?? [2, 10];
    const cost =
      ((usage.input_tokens ?? 0) * pin +
        (usage.cache_read_input_tokens ?? 0) * pin * 0.1 +
        (usage.cache_creation_input_tokens ?? 0) * pin * 1.25 +
        (usage.output_tokens ?? 0) * pout) /
      1e6;
    this.spentUsd += cost;
  }

  async #ollama(model, system, messages, json) {
    const res = await fetch(`${this.config.ollamaUrl}/api/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        model,
        stream: false,
        ...(json ? { format: 'json' } : {}),
        options: { num_ctx: this.config.ollamaCtx, temperature: 0.4 },
        messages: [{ role: 'system', content: system }, ...messages],
      }),
    });
    if (!res.ok) throw new Error(`ollama ${res.status}: ${await res.text()}`);
    const data = await res.json();
    return data.message?.content ?? '';
  }
}

/** Pull the first JSON object out of a model reply (tolerates ``` fences / chatter). */
export function parseJson(text) {
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) throw new Error('no JSON object in reply');
  return JSON.parse(text.slice(start, end + 1));
}
