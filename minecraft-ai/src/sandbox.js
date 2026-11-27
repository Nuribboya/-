import vm from 'node:vm';

// NOTE: node:vm is NOT a hard security boundary. This blocks the obvious escapes in
// model-written code, but run the bot in a throwaway container / dedicated machine.
const BANNED =
  /\b(process|require|import|eval|Function|constructor|globalThis|global|child_process|fs|__proto__|prototype|Reflect|Proxy)\b/;

const ASYNC_API = 'gather|craft|place|smelt|goTo|explore|attack|eat|sleep|useSkill';
const FLOATING = new RegExp(`^\\s*api\\.(${ASYNC_API})\\(`, 'm');

const stripLiterals = (code) =>
  code
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/.*$/gm, '')
    .replace(/'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`/g, '""');

export function checkCode(code, apiNames) {
  const bare = stripLiterals(code);
  if (/[\u3131-\u318E\uAC00-\uD7A3]/.test(bare))
    throw new Error('Korean text found in code outside strings/comments - identifiers and syntax must be English; Korean is allowed only inside quotes or comments');
  const m = code.match(BANNED);
  if (m) throw new Error(`code uses banned identifier "${m[0]}" - use only the api object`);
  if (apiNames) {
    for (const [, name] of bare.matchAll(/\bapi\.([A-Za-z_$][\w$]*)/g)) {
      if (!apiNames.includes(name))
        throw new Error(`api.${name} does not exist. Available api: ${apiNames.join(', ')}. Saved skills are NOT api functions - call them with: await api.useSkill("skill_name", {args})`);
    }
  }
  const f = code.match(FLOATING);
  if (f) throw new Error(`missing await: "api.${f[1]}(...)" must be written as "await api.${f[1]}(...)"`);
}

/**
 * Run a skill body: `async (api, args) => { <code> }` with a time limit.
 * `ctx` is shared with the api so abort stops further bot actions.
 */
export async function runSkill(code, api, args, ctx, timeoutMs = 120000) {
  checkCode(code, Object.keys(api));
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
