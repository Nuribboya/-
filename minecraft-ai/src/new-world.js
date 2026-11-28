// Start over in a new world but keep what was learned.
// Resets world progress (milestones, current/self goals, advancement notes, death count,
// lessons that mention coordinates). Keeps skills, the journal and general lessons.
// A brain snapshot is taken first, so this can be undone with `npm run brain -- restore <name>`.
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { Memory } from './memory.js';
import { Brains } from './brain.js';

export function resetForNewWorld(memory) {
  const d = memory.data;
  const before = { achieved: d.achieved?.length ?? 0, lessons: d.lessons?.length ?? 0 };
  d.achieved = [];
  d.freeGoal = null;
  d.goalHistory = [];
  d.advancementsFromChat = [];
  d.deaths = 0;
  // "(120,64,-30)" style coordinates mean nothing in another world.
  d.lessons = (d.lessons ?? []).filter((l) => !/-?\d+\s*,\s*-?\d+\s*,\s*-?\d+/.test(l));
  memory.save();
  return { clearedMilestones: before.achieved, droppedLessons: before.lessons - d.lessons.length, keptLessons: d.lessons.length };
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith('new-world.js')) {
  const snap = new Brains(config.dataDir, config.brainsDir).snapshot('before-new-world');
  const main = new Memory(config.dataDir);
  const r = resetForNewWorld(main);
  const botsDir = path.join(config.dataDir, 'bots');
  for (const name of fs.existsSync(botsDir) ? fs.readdirSync(botsDir) : []) {
    resetForNewWorld(new Memory(path.join(botsDir, name), { sharedLessons: main }));
    console.log(`- ${name} 진행 상황도 초기화`);
  }
  console.log(`새 월드 준비 완료 (백업: ${snap ?? '없음'})`);
  console.log(`- 마일스톤 ${r.clearedMilestones}개 초기화, 좌표가 들어간 교훈 ${r.droppedLessons}개 삭제, 교훈 ${r.keptLessons}개 유지`);
  console.log('- 배운 스킬과 일지는 그대로입니다. 되돌리려면: npm run brain -- restore ' + (snap ?? '<이름>'));
}
