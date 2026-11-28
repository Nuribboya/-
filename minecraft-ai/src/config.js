const env = process.env;

const ollama = (p) => p === 'ollama';
const fastProvider = env.FAST_PROVIDER ?? 'anthropic';
const smartProvider = env.SMART_PROVIDER ?? 'anthropic';

export const config = {
  host: env.MC_HOST ?? '127.0.0.1',
  port: Number(env.MC_PORT ?? 25565),
  username: env.MC_USER ?? 'ClaudeBot',
  // Several bots in the same world share skills, lessons and the journal (collective learning).
  botCount: Math.max(1, Math.min(8, Number(env.BOT_COUNT ?? 1))),
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
  // Keep the model in (GPU) memory between calls instead of Ollama's default 5 minutes.
  ollamaKeepAlive: env.OLLAMA_KEEP_ALIVE ?? '60m',

  dailyBudgetUsd: Number(env.DAILY_BUDGET_USD ?? 15),
  decisionIntervalMs: Number(env.DECISION_INTERVAL_MS ?? 300),
  // Autopilot: while the plan is going fine, run its next step without asking the model.
  // The model is still asked after a failure, in danger, without a plan, and every N turns.
  autopilot: env.AUTOPILOT !== 'false',
  torches: env.TORCHES !== 'false', // light up dark tunnels/caves every ~8 blocks
  autopilotMaxStreak: Number(env.AUTOPILOT_MAX_STREAK ?? 5),
  reflectEvery: Number(env.REFLECT_EVERY ?? 25), // decision steps between self-improvement passes
  skillTimeoutMs: Number(env.SKILL_TIMEOUT_MS ?? 120000),
  drownAfterMs: Number(env.DROWN_AFTER_MS ?? 7000), // head under water this long = swim up
  stuckMs: Number(env.STUCK_MS ?? 15000), // walking but not moving this long = stuck
  writeTestTimeoutMs: Number(env.WRITE_TEST_TIMEOUT_MS ?? 60000), // first trial run of new code
  overlayPort: Number(env.OVERLAY_PORT ?? 3000),
  dataDir: env.DATA_DIR ?? 'data',
  logMaxChars: Number(env.LOG_MAX_CHARS ?? 200), // console line length (full text in data/bot.log)
  clearEvery: Number(env.CLEAR_EVERY ?? 300), // clear console after this many lines (0 = never)
  writeCooldownSteps: Number(env.WRITE_COOLDOWN_STEPS ?? 6), // block write_skill after repeated write failures
  brainsDir: env.BRAINS_DIR ?? 'brains', // snapshots of dataDir
  snapshotEveryMin: Number(env.SNAPSHOT_EVERY_MIN ?? 60),
  consolidateEvery: Number(env.CONSOLIDATE_EVERY ?? 4), // every N reflection passes
  advancementGoals: env.ADVANCEMENT_GOALS !== 'false',
  netherPrep: env.NETHER_PREP !== 'false', // then: diamond pickaxe -> obsidian -> flint & steel -> lit portal // after the iron pickaxe, go for advancements first
  freeGoalMaxSteps: Number(env.FREE_GOAL_MAX_STEPS ?? 80), // give up a self-chosen goal after this many turns
  maxSkills: Number(env.MAX_SKILLS ?? 25), // learned skills kept active (builtins not counted)
};
