// Death pattern analysis: classify every death from the journal, find repeated patterns,
// and turn them into concrete behaviour changes ("policies") the bot applies by itself.
// The journal is shared between bots, so one bot's deaths teach all of them.

const MOB = ['zombie', 'skeleton', 'spider', 'creeper', 'mob'];

/** Death message (after the bot's name) -> category. */
export function classifyDeath(cause = '') {
  const c = cause.toLowerCase();
  if (/lava|burn|fire|flames|went up in/.test(c)) return 'lava';
  if (/blew up|blown up|creeper|explosion/.test(c)) return 'creeper';
  if (/^drowned|whilst trying to escape/.test(c)) return 'drown';
  if (/starved/.test(c)) return 'starve';
  if (/suffocat|in a wall|squashed/.test(c)) return 'suffocate';
  if (/fell|hit the ground|doomed to fall|fall damage/.test(c)) return 'fall';
  if (/shot|skeleton|arrow|stray/.test(c)) return 'skeleton';
  if (/zombie|husk|drowned/.test(c)) return 'zombie';
  if (/spider/.test(c)) return 'spider';
  if (/slain|killed|was hit/.test(c)) return 'mob';
  return 'other';
}

const KO = {
  lava: '용암', creeper: '크리퍼 폭발', drown: '익사', starve: '굶주림', suffocate: '질식', fall: '낙사',
  skeleton: '스켈레톤', zombie: '좀비', spider: '거미', mob: '몹', other: '기타',
};

export const DEFAULT_POLICY = {
  hostileRadius: 10, // autopilot hands control to the model when a hostile is this close
  creeperRadius: 10,
  nightSurfaceDanger: false, // night + open sky counts as danger
  gearFirst: false, // armor/shield before other self-chosen goals
  descendScan: 1, // how far (blocks) to the sides descend checks for lava
  maxDropDown: 4, // pathfinder: highest drop it will jump down
  foodMin: 6, // hunger at or below this counts as danger (eat first)
};

/**
 * Look at the last `window` deaths and derive patterns + the policy that answers them.
 * Returns { policy, patterns: [{ key, count, text, change }] }.
 */
export function analyzeDeaths(deaths, window = 12) {
  const recent = deaths.slice(-window).map((d) => ({ ...d, kind: d.kind ?? classifyDeath(d.cause) }));
  const policy = { ...DEFAULT_POLICY };
  const patterns = [];
  const count = (f) => recent.filter(f).length;
  const add = (key, n, text, change) => patterns.push({ key, count: n, text, change });

  const byMob = count((d) => MOB.includes(d.kind));
  const nightSurface = count((d) => MOB.includes(d.kind) && d.night && d.underground === false);
  if (nightSurface >= 2) {
    policy.nightSurfaceDanger = true;
    add('night_surface', nightSurface, `밤에 지상에서 몹에게 ${nightSurface}번`, '밤에 지상에 있으면 위험으로 보고 지하 작업/은신');
  }
  const creeper = count((d) => d.kind === 'creeper');
  if (creeper >= 2) {
    policy.creeperRadius = 16;
    add('creeper', creeper, `크리퍼 폭발 ${creeper}번`, '크리퍼 경계 거리 16칸');
  }
  if (byMob >= 3) {
    policy.hostileRadius = 14;
    add('mobs', byMob, `몹에게 ${byMob}번`, '적대 몹 경계 거리 14칸');
  }
  const noArmor = count((d) => MOB.includes(d.kind) && d.armor === 0);
  if (noArmor >= 2) {
    policy.gearFirst = true;
    add('no_armor', noArmor, `갑옷 없이 몹에게 ${noArmor}번`, '갑옷·방패를 먼저 목표로');
  }
  const lava = count((d) => d.kind === 'lava');
  if (lava >= 1) {
    policy.descendScan = 2;
    add('lava', lava, `용암 ${lava}번`, '파 내려갈 때 용암 확인 범위 넓힘');
  }
  const fall = count((d) => d.kind === 'fall');
  if (fall >= 2) {
    policy.maxDropDown = 2;
    add('fall', fall, `낙사 ${fall}번`, '2칸 넘게 뛰어내리지 않기');
  }
  const starve = count((d) => d.kind === 'starve');
  if (starve >= 1) {
    policy.foodMin = 12;
    add('starve', starve, `굶어 죽음 ${starve}번`, '허기 12부터 먼저 먹기');
  }
  for (const k of ['drown', 'suffocate']) {
    const n = count((d) => d.kind === k);
    if (n >= 2) add(k, n, `${KO[k]} ${n}번`, k === 'drown' ? '물속에서 오래 머물지 않기' : '모래·자갈 아래에서 파지 않기');
  }
  return { policy, patterns };
}

/** Short text for the decision prompt. */
export function patternsText(analysis) {
  if (!analysis.patterns.length) return '';
  return `죽음 패턴 분석 (자동 적용 중):\n${analysis.patterns.map((p) => `- ${p.text} → ${p.change}`).join('\n')}`;
}

export const DEATH_KO = KO;
