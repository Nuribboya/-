import fs from 'node:fs';
import path from 'node:path';

const MAX_LESSONS = 15;

/**
 * Small persistent memory: lessons learned, deaths, achieved milestones, goals.
 * With `sharedLessons` (another Memory), lessons live in that one and are shared by every bot
 * using it; everything else (progress, deaths, goals) stays per bot.
 */
export class Memory {
  constructor(dataDir, { sharedLessons = null } = {}) {
    this.file = path.join(dataDir, 'memory.json');
    fs.mkdirSync(dataDir, { recursive: true });
    this.data = { lessons: [], deaths: 0, achieved: [] };
    try {
      this.data = { ...this.data, ...JSON.parse(fs.readFileSync(this.file, 'utf8')) };
    } catch {}
    this.shared = sharedLessons;
    if (sharedLessons) {
      delete this.data.lessons;
      // Not enumerable: not written to this bot's file, read/written on the shared memory.
      Object.defineProperty(this.data, 'lessons', {
        get: () => sharedLessons.data.lessons,
        set: (v) => (sharedLessons.data.lessons = v),
        enumerable: false,
        configurable: true,
      });
    }
  }

  save() {
    fs.writeFileSync(this.file, JSON.stringify(this.data, null, 2));
    this.shared?.save();
  }

  addLesson(text) {
    this.data.lessons = [...this.data.lessons.filter((l) => l !== text), text].slice(-MAX_LESSONS);
    this.save();
  }

  addDeath() {
    this.data.deaths++;
    this.save();
  }

  achieve(id) {
    if (!this.data.achieved.includes(id)) {
      this.data.achieved.push(id);
      this.save();
      return true;
    }
    return false;
  }
}
