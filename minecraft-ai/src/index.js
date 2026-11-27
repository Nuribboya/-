import mineflayer from 'mineflayer';
import pf from 'mineflayer-pathfinder';
import { config } from './config.js';
import { LLM } from './llm.js';
import { Skills } from './skills.js';
import { Memory } from './memory.js';
import { Events } from './events.js';
import { Agent } from './agent.js';
import { startOverlay } from './overlay.js';

const { pathfinder, Movements } = pf;

const events = new Events();
const llm = new LLM(config);
const skills = new Skills(config.dataDir);
const memory = new Memory(config.dataDir);
events.update({ deaths: memory.data.deaths, skills: skills.count() });
startOverlay(events, config.overlayPort);

console.log(`빠른 두뇌: ${config.fast.provider}/${config.fast.model} | 똑똑한 두뇌: ${config.smart.provider}/${config.smart.model} | 하루 예산 $${config.dailyBudgetUsd}`);

function connect() {
  events.log(`접속 중: ${config.host}:${config.port}`);
  const bot = mineflayer.createBot({
    host: config.host,
    port: config.port,
    username: config.username,
    version: config.version,
  });
  bot.loadPlugin(pathfinder);

  let agent;
  bot.once('spawn', () => {
    bot.pathfinder.setMovements(new Movements(bot));
    events.log('스폰 완료. 플레이 시작!');
    agent = new Agent({ bot, llm, skills, memory, events, config });
    agent.run();
  });

  bot.on('kicked', (r) => events.log(`추방됨: ${typeof r === 'string' ? r : JSON.stringify(r)}`));
  bot.on('error', (e) => events.log(`연결 오류: ${e.message}`));
  bot.on('end', () => {
    agent?.stop();
    events.log('연결 끊김. 10초 후 재접속...');
    setTimeout(connect, 10000);
  });
}

connect();
