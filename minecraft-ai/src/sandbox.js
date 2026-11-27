import vm from 'node:vm';

// NOTE: node:vm is NOT a hard security boundary. This blocks the obvious escapes in
// model-written code, but run the bot in a throwaway container / dedicated machine.
const BANNED =
  /\b(process|require|import|eval|Function|constructor|globalThis|global|child_process|fs|__proto__|prototype|Reflect|Proxy)\b/;

export function checkCode(code) {
  const m = code.match(BANNED);
  if (m) throw new Error(`code uses banned identifier "${m[0]}" - use only the api object`);
}

/**
 * Run a skill body: `async (api, args) => { <code> }` with a time limit.
 * `ctx` is shared with the api so abort stops further bot actions.
 */
export async function runSkill(code, api, args, ctx, timeoutMs = 120000) {
  checkCode(code);
  const fn = vm.runInNewContext(`(async (api, args) => {\n${code}\n})`, Object.create(null), { timeout: 1000 });

  let timer;
  const timeout = new Promise((_, rej) => {
    timer = setTimeout(() => {
      ctx.aborted = true;
      rej(new Error(`skill exceeded ${timeoutMs / 1000}s`));
    }, timeoutMs);
  });
  try {
    return await Promise.race([fn(api, args ?? {}), timeout]);
  } finally {
    clearTimeout(timer);
  }
}
