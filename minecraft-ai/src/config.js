const env = process.env;

const ollama = (p) => p === 'ollama';
const fastProvider = env.FAST_PROVIDER ?? 'anthropic';
const smartProvider = env.SMART_PROVIDER ?? 'anthropic';

export const config = {
  host: env.MC_HOST ?? '127.0.0.1',
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
  ollamaCtx: Number(env.OLLAMA_CTX ?? 8192), // Ollama's default 2048 would cut off the prompt
  ollamaUrl: env.OLLAMA_URL ?? 'http://localhost:11434',

  dailyBudgetUsd: Number(env.DAILY_BUDGET_USD ?? 15),
  decisionIntervalMs: Number(env.DECISION_INTERVAL_MS ?? 2000),
  reflectEvery: Number(env.REFLECT_EVERY ?? 25), // decision steps between self-improvement passes
  skillTimeoutMs: Number(env.SKILL_TIMEOUT_MS ?? 120000),
  writeTestTimeoutMs: Number(env.WRITE_TEST_TIMEOUT_MS ?? 60000), // first trial run of new code
  overlayPort: Number(env.OVERLAY_PORT ?? 3000),
  dataDir: env.DATA_DIR ?? 'data',
  logMaxChars: Number(env.LOG_MAX_CHARS ?? 200), // console line length (full text in data/bot.log)
  clearEvery: Number(env.CLEAR_EVERY ?? 300), // clear console after this many lines (0 = never)
  writeCooldownSteps: Number(env.WRITE_COOLDOWN_STEPS ?? 6), // block write_skill after repeated write failures
  brainsDir: env.BRAINS_DIR ?? 'brains', // snapshots of dataDir
  snapshotEveryMin: Number(env.SNAPSHOT_EVERY_MIN ?? 60),
  consolidateEvery: Number(env.CONSOLIDATE_EVERY ?? 4), // every N reflection passes
  maxSkills: Number(env.MAX_SKILLS ?? 25), // learned skills kept active (builtins not counted)
};
