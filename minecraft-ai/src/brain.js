import fs from 'node:fs';
import path from 'node:path';

const pad = (n) => String(n).padStart(2, '0');
const stamp = () => {
  const d = new Date();
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
};

// Labels whose snapshots are rotated (only the newest N kept). Milestone/manual ones are kept forever.
const ROTATED = ['auto', 'before-consolidate', 'before-restore'];

/** "Brain" snapshots: full copies of the data dir (skills, lessons, journal) in brainsDir/brain-<time>-<label>/ */
export class Brains {
  constructor(dataDir, brainsDir, keep = 10) {
    this.dataDir = dataDir;
    this.dir = brainsDir;
    this.keep = keep;
    fs.mkdirSync(this.dir, { recursive: true });
  }

  snapshot(label = 'manual') {
    if (!fs.existsSync(this.dataDir)) return null;
    const safe = String(label).replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 40) || 'manual';
    // Keep the label at the end so rotation (endsWith) still matches on a same-second collision.
    const st = stamp();
    let name = `brain-${st}-${safe}`;
    for (let i = 2; fs.existsSync(path.join(this.dir, name)); i++) name = `brain-${st}.${i}-${safe}`;
    fs.cpSync(this.dataDir, path.join(this.dir, name), { recursive: true });
    this.#rotate();
    return name;
  }

  list() {
    return fs.readdirSync(this.dir).filter((n) => n.startsWith('brain-')).sort();
  }

  #rotate() {
    for (const label of ROTATED) {
      const group = this.list().filter((n) => n.endsWith(`-${label}`));
      for (const n of group.slice(0, Math.max(0, group.length - this.keep)))
        fs.rmSync(path.join(this.dir, n), { recursive: true, force: true });
    }
  }

  /** Replace the data dir with a snapshot. The current data is snapshotted first. */
  restore(name) {
    const src = path.join(this.dir, name);
    if (!name || !fs.existsSync(src)) throw new Error(`no such brain: ${name}`);
    const backup = this.snapshot('before-restore');
    fs.rmSync(this.dataDir, { recursive: true, force: true });
    fs.cpSync(src, this.dataDir, { recursive: true });
    return backup;
  }
}
