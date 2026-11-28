// Single source of truth for which api functions are "body actions":
// async, run one at a time, must be awaited, and count as "the skill did something".
export const BODY_ACTIONS = [
  'gather', 'craft', 'place', 'smelt', 'goTo', 'explore', 'attack', 'eat',
  'placeAt', 'digAt', 'equip', 'block', 'descend', 'exploreCave', 'shelter', 'sleepInBed',
];
// Async but not queued (sleep) or queue-free by design (useSkill calls body actions itself).
export const OTHER_ASYNC = ['sleep', 'useSkill'];
