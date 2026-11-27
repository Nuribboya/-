import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';

const MAX_LOG_BYTES = 5 * 1024 * 1024;

/**
 * Shared live state for the stream overlay + console logging.
 * Console is kept light (long lines cut, repeats collapsed, screen cleared now and then);
 * the full untruncated log goes to <dataDir>/bot.log.
 */
export class Events extends EventEmitter {
  constructor({ dataDir, maxChars = 200, clearEvery = 300 } = {}) {
    super();
    this.state = {
      thought: '시작하는 중...',
      goal: '',
      lastAction: '',
      deaths: 0,
      skills: 0,
      spentUsd: 0,
      startedAt: Date.now(),
      log: [],
    };
    this.maxChars = maxChars;
    this.clearEvery = clearEvery;
    this.printed = 0;
    this.lastLine = null;
    this.repeats = 0;
    this.file = dataDir ? path.join(dataDir, 'bot.log') : null;
    if (this.file) fs.mkdirSync(dataDir, { recursive: true });
  }

  update(patch) {
    Object.assign(this.state, patch);
    this.emit('update', this.state);
  }

  #toFile(text) {
    if (!this.file) return;
    try {
      if (fs.existsSync(this.file) && fs.statSync(this.file).size > MAX_LOG_BYTES) {
        fs.renameSync(this.file, this.file + '.old'); // keep one previous file
      }
      fs.appendFileSync(this.file, text + '\n');
    } catch {}
  }

  #print(text) {
    if (this.clearEvery && this.printed >= this.clearEvery) {
      console.clear();
      console.log(`── 화면 정리됨 (전체 기록: ${this.file ?? '없음'}) ──`);
      this.printed = 0;
    }
    console.log(text.length > this.maxChars ? text.slice(0, this.maxChars) + '…' : text);
    this.printed++;
  }

  log(line) {
    const t = new Date().toLocaleTimeString('ko-KR', { hour12: false });
    this.#toFile(`[${t}] ${line}`);
    if (line === this.lastLine) {
      this.repeats++;
      return;
    }
    if (this.repeats) this.#print(`   (위 메시지 ${this.repeats}번 더 반복)`);
    this.repeats = 0;
    this.lastLine = line;
    this.#print(`[${t}] ${line}`);
    this.state.log = [...this.state.log.slice(-7), line];
    this.emit('update', this.state);
  }
}
