// Small helpers shared by several modules (kept here so they exist exactly once).

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Shorten any value to n chars for prompts and logs. */
export const short = (v, n = 400) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s && s.length > n ? s.slice(0, n) + '…' : s;
};

/** Minecraft night: hostile mobs spawn, beds can be used. */
export const isNightTime = (timeOfDay) => timeOfDay >= 13000 && timeOfDay < 23000;
export const isNight = (bot) => isNightTime(bot.time?.timeOfDay ?? 0);

/** Reject with `message` if `promise` takes longer than ms (the promise itself keeps running). */
export function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, rej) => {
      timer = setTimeout(() => rej(new Error(message)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}
