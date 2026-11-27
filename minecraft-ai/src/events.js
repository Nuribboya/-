import { EventEmitter } from 'node:events';

/** Shared live state for the stream overlay. */
export class Events extends EventEmitter {
  constructor() {
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
  }

  update(patch) {
    Object.assign(this.state, patch);
    this.emit('update', this.state);
  }

  log(line) {
    const t = new Date().toLocaleTimeString('ko-KR', { hour12: false });
    console.log(`[${t}] ${line}`);
    this.state.log = [...this.state.log.slice(-7), line];
    this.emit('update', this.state);
  }
}
