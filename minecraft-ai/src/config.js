const env = process.env;

const ollama = (p) => p === 'ollama';
const fastProvider = env.FAST_PROVIDER ?? 'anthropic';
const smartProvider = env.SMART_PROVIDER ?? 'anthropic';

export const config = {
  host: env.MC_HOST ?? 'localhost',
  port: Number(env.MC_PORT ?? 25565),
  username: env.MC_USER ?? 'ClaudeBot',
  version: env.MC_VERSION || undefined,

  // "fast" = routine decisions (many calls). "smart" = writing/fixing skill code (few calls).
  fast: {
    provider: fastProvider,
    model: env.FAST_MODEL ?? (ollama(fastProvider) ? 'qwen2.5:7b' : 'claude-haiku-5-5'),
  },
  smart: {
    provider: smartProvider,
    model: env.SMART_MODEL ?? (ollama(smartProvider) ? 'qwen2.5-coder:14b' : 'claude-sonnet-5-5'),
  },
  ollamaUrl: env.OLLAMA_URL ?? 'http://localhost:11434',

  dailyBudgetUsd: Number(env.DAILY_BUDGET_USD ?? 15),
  decisionIntervalMs: Number(env.DECISION_INTERVAL_MS ?? 2000),
  skillTimeoutMs: Number(env.SKILL_TIMEOUT_MS ?? 120000),
  overlayPort: Number(env.OVERLAY_PORT ?? 3000),
  dataDir: env.DATA_DIR ?? 'data',
};
