import fs from 'node:fs';
import path from 'node:path';

const MAX_LESSONS = 15;

/** Small persistent memory: lessons learned, deaths, achieved milestones. */
export class Memory {
  constructor(dataDir) {
    this.file = path.join(dataDir, 'memory.json');
    fs.mkdirSync(dataDir, { recursive: true });
    this.data = { lessons: [], deaths: 0, achieved: [] };
    try {
      this.data = { ...this.data, ...JSON.parse(fs.readFileSync(this.file, 'utf8')) };
    } catch {}
  }

  save() {
    fs.writeFileSync(this.file, JSON.stringify(this.data, null, 2));
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
