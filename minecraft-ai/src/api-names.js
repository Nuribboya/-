// Single source of truth for which api functions are "body actions":
// async, run one at a time, must be awaited, and count as "the skill did something".
export const BODY_ACTIONS = [
  'gather', 'craft', 'place', 'smelt', 'goTo', 'explore', 'attack', 'eat',
  'placeAt', 'digAt', 'equip', 'block', 'descend', 'ascend', 'exploreCave', 'shelter', 'sleepInBed', 'recoverDrops', 'unstuck', 'fillBucket', 'pourBucket', 'makeObsidian', 'buildPortal',
];
// Async but not queued: sleep, and useSkill/followPlan which call body actions themselves
// (queuing them would deadlock).
export const OTHER_ASYNC = ['sleep', 'useSkill', 'followPlan'];
