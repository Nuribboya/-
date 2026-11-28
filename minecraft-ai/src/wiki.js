// Practical Minecraft know-how that game data does not contain (mob tactics, safety, priorities).
// Written as short notes in our own words (not copied from the wiki). Each turn only the notes
// relevant to the current situation are put in the prompt, so a small model is not flooded.

const NOTES = [
  {
    topic: '크리퍼',
    keys: ['creeper', '크리퍼', '폭발', 'blew up', 'explosion'],
    text: '크리퍼(creeper): 가까이 가면 1.5초 뒤 터진다. 근접전 금지. 다가오면 뒤로 물러나 거리 벌리기(nearbyMobs로 위치 확인 후 반대 방향 goTo). 터지면 주변 블록과 아이템도 날아간다.',
  },
  {
    topic: '스켈레톤',
    keys: ['skeleton', '스켈레톤', 'shot by', 'arrow', '화살'],
    text: '스켈레톤(skeleton): 멀리서 화살을 쏜다. 탁 트인 곳에서 상대하지 말고 블록/나무 뒤로 숨거나 지하로. 방패(shield)를 off-hand에 들고 block()으로 막을 수 있다. 낮에는 햇빛에 불탄다.',
  },
  {
    topic: '좀비',
    keys: ['zombie', '좀비', 'slain by zombie', 'husk', 'drowned'],
    text: '좀비(zombie): 느리고 근접 공격만 한다. 칼/도끼로 상대 가능(attack). 여러 마리면 물러나기. 썩은 살점(rotten_flesh)은 배고픔 부작용이 있어 비상식량으로만.',
  },
  {
    topic: '거미',
    keys: ['spider', '거미'],
    text: '거미(spider): 벽을 타고 오른다. 낮에는 먼저 공격하지 않는다. 잡으면 실(string) - 실 4개로 양털, 활 재료.',
  },
  {
    topic: '엔더맨',
    keys: ['enderman', '엔더맨'],
    text: '엔더맨(enderman): 눈을 마주치면(바라보면) 공격해 온다. 쳐다보지 말 것. 물에 약하다. 2칸 높이 미만 굴로 숨으면 못 따라온다.',
  },
  {
    topic: '밤과 몹 스폰',
    keys: ['밤', 'night', '어두', 'dark', 'torch', '횃불', 'shelter', '은신'],
    text: '적대 몹은 어두운 곳(빛 0)에 생긴다: 밤의 지표, 동굴. 횃불(torch = coal 또는 charcoal 1 + stick 1 → 4개)로 주변을 밝히면 덜 생긴다. 석탄이 없으면 원목을 화로에 구워 charcoal.',
  },
  {
    topic: '음식',
    keys: ['배고픔', 'eat_food', '음식', 'cooked_', 'beef', 'porkchop', 'mutton'],
    text: '음식: 고기를 화로에 구우면 날것보다 훨씬 많이 회복된다. 소/돼지/닭/양을 잡아 고기(hunt_animal) → smelt_item. 사과(apple)는 참나무 잎에서 가끔 나온다. 허기 6 이하면 달리기가 안 되고, 0이면 체력이 깎인다.',
  },
  {
    topic: '농사',
    keys: ['wheat', '밀', 'bread', '빵', 'seed', '씨앗', 'farm', '농장', 'hoe'],
    text: '농사: 풀을 부수면 밀 씨앗(wheat_seeds)이 나온다. 괭이(hoe)로 물 근처 흙을 갈고 씨앗을 심으면 자란다. 밀 3개 = 빵(bread). 자라는 데 시간이 걸리니 동물 사냥이 더 빠르다.',
  },
  {
    topic: '광질 안전',
    keys: ['descend', 'mine', '광질', '광석', 'ore', 'lava', '용암', 'cave', '동굴', 'gravel', '자갈', 'diamond', '다이아'],
    text: '광질 안전: 바로 아래로 파지 말 것(용암/낭떠러지) - descend_mine은 계단식이라 안전. y≈-58 근처 다이아 층엔 용암 호수가 흔하다. 모래/자갈은 위에서 떨어진다. 용암 근처에선 방향을 바꾼다. 다이아(diamond_ore)는 iron_pickaxe 이상으로만 캘 수 있다.',
  },
  {
    topic: '갑옷',
    keys: ['armor', '갑옷', 'chestplate', 'helmet', 'leggings', 'boots'],
    text: '갑옷: 철 갑옷 = 흉갑(iron_chestplate) 철 8, 각반(iron_leggings) 7, 투구(iron_helmet) 5, 부츠(iron_boots) 4 - 풀세트 철 24개. 방어력이 큰 흉갑부터. 만들면 api.equip으로 입어야 효과가 있다(chest→torso, legs, head, feet). 철이 부족하면 가죽 갑옷(leather, 소)도 도움된다.',
  },
  {
    topic: '방패와 무기',
    keys: ['shield', '방패', '_sword', '칼', '_axe', '도끼', 'weapon', '무기', 'attack', '전투'],
    text: '무기/방패: 칼(sword)은 빠르고 도끼(axe)는 한 방이 세다. 방패(shield) = 판자 6 + 철 1. off-hand에 장착(api.equip("shield","off-hand")) 후 api.block()으로 화살·근접 공격을 막는다.',
  },
  {
    topic: '도구 우선순위',
    keys: ['iron_ingot', 'raw_iron', 'bucket', '양동이', 'shears', '가위'],
    text: '철 우선순위(보통): 철 곡괭이(3) → 양동이(bucket, 3: 물로 용암 끄기/떨어질 때 착지) → 방패(1) → 흉갑(8) → 나머지 갑옷. 가위(shears, 2)로 양털을 잡지 않고 얻을 수 있다.',
  },
  {
    topic: '침대와 부활',
    keys: ['bed', '침대', 'death', '죽', 'respawn', 'spawn', 'wool', '양털'],
    text: '침대: 같은 색 양털 3 + 판자 3. 침대에서 자면 부활 지점이 침대가 된다(죽어도 가까이서 시작). 죽으면 인벤토리를 그 자리에 떨어뜨리고, 5분 안에 가서 주우면 되찾는다.',
  },
  {
    topic: '보관',
    keys: ['chest"', 'chest ', '상자', '보관'],
    text: '상자(chest) = 판자 8. 귀한 물건(철, 다이아)은 상자에 보관하면 죽어도 잃지 않는다. 인벤토리는 36칸.',
  },
  {
    topic: '네더 준비',
    keys: ['nether', '네더', 'obsidian', '흑요석', 'portal', '포탈', 'flint', '부싯돌'],
    text: '네더: 포탈 틀은 흑요석(obsidian) 14개(네 모서리를 빼면 10개), flint_and_steel(철 1 + 부싯돌 1)로 점화. 흑요석은 다이아 곡괭이로만 캔다(물+용암으로 생김). 네더는 위험 - 철 갑옷과 음식 충분히 준비.',
  },
  {
    topic: '마법 부여',
    keys: ['enchant', '마법', 'book', '책', 'lapis', '청금석', 'experience', '경험치'],
    text: '마법부여대(enchanting_table) = 책 1 + 다이아 2 + 흑요석 4. 청금석(lapis_lazuli)과 경험치 레벨이 필요하다. 경험치는 몹 처치·광석 채굴·굽기로 쌓인다.',
  },
];

/** Food values from game data: best foods by hunger + saturation. */
function foodNote(reg) {
  const foods = Object.values(reg?.foodsByName ?? {});
  if (!foods.length) return null;
  const best = foods
    .filter((f) => !/golden|suspicious|enchanted|stew|soup/.test(f.name))
    .sort((a, b) => b.foodPoints + b.saturation - (a.foodPoints + a.saturation))
    .slice(0, 6)
    .map((f) => `${f.name}(${f.foodPoints})`)
    .join(', ');
  return { topic: '음식 데이터', keys: ['배고픔', 'eat_food', '음식'], text: `회복량 좋은 음식(게임 데이터, 허기 회복): ${best}.` };
}

/**
 * Pick the notes that match the situation (goal, observation, last result...).
 * Returns prompt text, at most `max` notes and ~maxChars characters.
 */
export function wikiFor(context, reg = null, { max = 3, maxChars = 900 } = {}) {
  const ctx = String(context).toLowerCase();
  const notes = [...NOTES];
  const food = foodNote(reg);
  if (food) notes.push(food);
  const scored = notes
    .map((n) => ({ n, score: n.keys.reduce((s, k) => (ctx.includes(k.toLowerCase()) ? s + 1 : s), 0) }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score);
  const out = [];
  let len = 0;
  for (const { n } of scored) {
    if (out.length >= max || len + n.text.length > maxChars) break;
    out.push(`- ${n.text}`);
    len += n.text.length;
  }
  return out.length ? `참고 지식 (상황 관련):\n${out.join('\n')}` : '';
}

export const WIKI_TOPICS = NOTES.map((n) => n.topic);
