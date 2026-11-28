import mineflayer from 'mineflayer';
import pf from 'mineflayer-pathfinder';
import { config } from './config.js';
import { LLM } from './llm.js';
import { Skills } from './skills.js';
import { Memory } from './memory.js';
import { Events } from './events.js';
import { Agent } from './agent.js';
import { Journal } from './journal.js';
import { startOverlay } from './overlay.js';
import path from 'node:path';

const { pathfinder, Movements } = pf;

const events = new Events({ dataDir: config.dataDir, maxChars: config.logMaxChars, clearEvery: config.clearEvery });

// Never let a stray rejection from model-written code kill the bot.
process.on('unhandledRejection', (e) => events.log(`⚠️ 처리 안 된 오류 무시: ${e?.message ?? e}`));
process.on('uncaughtException', (e) => events.log(`⚠️ 예외 무시: ${e?.message ?? e}`));
// Shared by every bot: one model, one skill library, one lesson list, one journal.
const llm = new LLM(config);
const skills = new Skills(config.dataDir);
const memory = new Memory(config.dataDir); // bot 1's progress + the shared lessons
const journal = new Journal(config.dataDir);
events.update({ deaths: memory.data.deaths, skills: skills.count() });
startOverlay(events, config.overlayPort);

console.log(`빠른 두뇌: ${config.fast.provider}/${config.fast.model} | 똑똑한 두뇌: ${config.smart.provider}/${config.smart.model} | 하루 예산 $${config.dailyBudgetUsd}`);
if (config.botCount > 1) console.log(`봇 ${config.botCount}마리: 스킬·교훈·일지를 함께 씁니다 (진행 상황은 각자)`);

const multi = config.botCount > 1;
const bots = Array.from({ length: config.botCount }, (_, i) => {
  const name = i === 0 ? config.username : `${config.username}_${i + 1}`;
  return {
    name,
    // Each bot has its own inventory, so its own progress; lessons come from the shared memory.
    memory: i === 0 ? memory : new Memory(path.join(config.dataDir, 'bots', name), { sharedLessons: memory }),
    journal: multi ? journal.forBot(name) : journal,
    events: multi
      ? {
          log: (m) => events.log(`[${name}] ${m}`),
          update: (p) => events.update(p.thought !== undefined ? { ...p, thought: `[${name}] ${p.thought}` } : p),
        }
      : events,
  };
});

// Log in one by one: the LAN server dislikes several logins at the same moment.
bots.forEach((b, i) => setTimeout(() => connect(b), i * 4000));

function connect({ name, memory, journal, events }) {
  events.log(`접속 중: ${config.host}:${config.port} (${name})`);
  const bot = mineflayer.createBot({
    host: config.host,
    port: config.port,
    username: name,
    version: config.version,
  });
  bot.loadPlugin(pathfinder);

  let agent;
  bot.once('spawn', () => {
    const movements = new Movements(bot);
    // Pathfinder pillars up with dirt + cobblestone by default. Cobblestone is needed for the
    // furnace and stone tools, so build with junk blocks instead.
    movements.scafoldingBlocks = ['dirt', 'cobbled_deepslate', 'andesite', 'diorite', 'granite', 'tuff', 'netherrack']
      .map((n) => bot.registry.itemsByName[n]?.id)
      .filter((id) => id !== undefined);
    // Digging through terrain costs more than walking around it (default 1 makes the bot tunnel
    // through every hill). Mining still works: the target block itself is dug either way.
    movements.digCost = 4;
    // Water paths cost as much as land by default, so the bot happily walks across the sea floor
    // and drowns. Prefer land; water only when there is no other way.
    movements.liquidCost = 30;
    bot.pathfinder.setMovements(movements);
    bot.__movements = movements; // death-pattern policy adjusts it (e.g. maxDropDown)
    events.log('스폰 완료. 플레이 시작!');
    agent = new Agent({ bot, llm, skills, memory, events, config, journal });
    agent.run();
  });

  bot.on('kicked', (r) => events.log(`추방됨: ${typeof r === 'string' ? r : JSON.stringify(r)}`));
  bot.on('error', (e) => {
    const parts = [e.message, e.code, ...(e.errors ?? []).map((x) => `${x.address}:${x.port} ${x.code}`)].filter(Boolean);
    events.log(`연결 오류: ${parts.join(' | ') || String(e)}`);
    if (parts.some((p) => /ECONNREFUSED/.test(p)))
      events.log('→ 해당 포트에 열린 서버가 없음: 게임에서 LAN이 켜져 있는지, 채팅창의 포트 번호가 MC_PORT와 같은지 확인');
  });
  bot.on('end', () => {
    agent?.stop();
    events.log('연결 끊김. 10초 후 재접속...');
    setTimeout(() => connect({ name, memory, journal, events }), 10000);
  });
}
