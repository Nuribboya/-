import { config } from './config.js';
import { Brains } from './brain.js';

const brains = new Brains(config.dataDir, config.brainsDir);
const [cmd, arg] = process.argv.slice(2);

switch (cmd) {
  case 'save': {
    const name = brains.snapshot(arg ?? 'manual');
    console.log(name ? `저장됨: ${name}` : '저장할 data 폴더가 없음');
    break;
  }
  case 'restore': {
    if (!arg) {
      console.log('사용법: npm run brain -- restore <두뇌 이름>   (봇을 끈 상태에서)');
      process.exit(1);
    }
    const backup = brains.restore(arg);
    console.log(`복원됨: ${arg}\n(복원 직전 상태는 ${backup ?? '없음'} 으로 백업됨)`);
    break;
  }
  default: {
    const list = brains.list();
    console.log(list.length ? list.join('\n') : '저장된 두뇌 없음');
    console.log('\n명령: npm run brain -- save [이름] | npm run brain -- restore <이름>');
  }
}
