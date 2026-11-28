export const API_DOC = `
skill 코드에서 쓸 수 있는 객체는 \`api\` 와 \`args\` 뿐이다. 모든 메서드는 async (await 필수):
- api.gather(name, count=1, maxDistance=48): 블록을 캔다. name='log' 는 모든 종류의 원목. 예: 'cobblestone','coal_ore','iron_ore','diamond_ore'. 도구가 부족하면 에러.
- api.craft(item, times=1): 제작. 작업대가 필요하면 근처 작업대를 자동 사용 (없으면 에러)
- api.place(blockName): 인벤토리 블록을 주변 땅에 설치
- api.smelt(inputName, count=1, fuel='coal'): 근처 화로에서 굽기 (예: 'raw_iron')
- api.goTo(x,y,z,range=1) / api.explore(distance=30)
- api.attack(entityName='zombie'): 가장 가까운 해당 몹과 전투
- api.placeAt(x,y,z, blockName): 정확한 좌표에 블록 설치 (옆에 단단한 블록이 있어야 함, 내 몸 위치(발/머리)에는 불가)
- api.digAt(x,y,z): 정확한 좌표의 블록 캐기 (예: 발밑 파고 숨기 = api.digAt(p.x, p.y-1, p.z))
- api.descend(targetY=-58): 계단식으로 파 내려가기 (용암/물/낭떠러지 감지하면 에러로 멈춤)
- api.ascend(targetY=70): 지상으로 올라가기
- api.exploreCave(maxDistance=32): 근처 동굴 안으로 이동
- api.shelter(blockName?): 땅 3칸 파고 들어가 위를 막는 은신처 (위급할 때)
- api.sleepInBed(): 밤에 근처 침대(없으면 인벤토리 침대를 설치)에서 자기
- api.blockArrows(seconds=8): 가까운 스켈레톤을 바라보며 왼손 방패로 화살 막기
- api.fillBucket(liquid='water'): 빈 양동이로 물/용암 원천 뜨기
- api.pourBucket(x,y,z, bucketName='water_bucket'): 그 칸에 물(또는 용암) 붓기
- api.makeObsidian(count=10): 흑요석 얻기 (자연산 캐기 → 없으면 용암에 물 부어 만들기, 다이아 곡괭이 필요)
- api.buildPortal(): 네더 포탈 틀 짓고 불 붙이기 (흑요석 10, 모서리 블록 4, 부싯돌과 부시)
- api.equip(itemName, slot='hand'): 장착. slot = hand | off-hand | head | torso | legs | feet (갑옷, 방패, 칼)
- api.block(ms=1500): 왼손(off-hand) 방패 들기, 최대 5초. 먼저 await api.equip('shield','off-hand')
- api.eat()
- api.followPlan(steps=4): 자동 계획(현재 목표)의 다음 단계들을 연달아 실행 (await 필수)
- 저장된 스킬 호출: await api.스킬이름({인자}) 예) await api.collect_logs({count: 3}). 또는 await api.useSkill('collect_logs', {count: 3}). '보유 스킬' 목록에 있는 이름만 가능. 작은 스킬을 조합해 큰 스킬을 만들 때 쓴다.
- 지식 함수(동기, 게임 데이터): api.recipe(item) -> {ingredients, makes, requiresTable}, api.toolFor(block) -> 필요한 최소 곡괭이, api.plan(item, count) -> 단계별 계획 문자열 배열
- 동기 함수: api.isNight(), api.nearbyMobs(maxDistance=16) -> [{name,x,y,z,dist,hostile}] (가까운 순), api.inventory() -> {itemName: count}, api.has(name,n), api.countBlocks(name,maxDistance), api.position(), api.health(), api.food(), api.log(msg), api.chat(msg)
- api.sleep(ms)
활용 예: 도망 = 가장 가까운 적대몹 좌표의 반대 방향으로 api.goTo. 은신처 = 내 주변 4방향과 머리 위(y+2)에 api.placeAt으로 블록(dirt, cobblestone 등). 갑옷이 있으면 api.equip으로 입기.
규칙: 코드의 변수명/문법은 영어만. 한글은 따옴표 문자열이나 주석 안에서만 쓴다. 위 목록에 없는 api.* 는 존재하지 않는다. require/import/process/eval/Function/constructor 사용 금지. 반드시 \`return\` 으로 결과(인벤토리 등)를 돌려줄 것. 무한루프 금지, 반복문에는 await 포함. 아이템/블록 이름은 마인크래프트 영문 ID(snake_case).
`.trim();

export const DECISION_SYSTEM = `너는 마인크래프트(자바 에디션)를 스스로 플레이하며 배우는 AI다. 맨손에서 시작해 다이아몬드까지 가는 것이 목표다.
매 턴 현재 상태를 보고 다음 행동 하나를 고른다. 반드시 아래 JSON 한 개만 출력한다 (설명/코드펜스 금지):

{"thought": "시청자에게 보여줄 한두 문장 생각 (한국어, 짧고 재미있게)",
 "skill": "실행할 기존 스킬 이름", "args": {...}}
또는
{"thought": "...", "write_skill": "새로 만들거나 고칠 스킬이 해야 할 일을 구체적으로 설명 (기존 스킬을 고치려면 이름 포함)"}
또는
{"thought": "...", "wait": true}

판단 원칙:
- 생존 우선: 체력 8 이하, 허기 6 이하, 밤에 적대 몹이 가까우면 먼저 대응 (먹기/싸우기/도망/은신).
- 밤에도 낮과 똑같이 지금 목표를 계속한다 (지상 작업 포함). 밤이라는 이유로 땅을 파고 들어가거나 숨지 않는다. 적대 몹이 다가오면 싸우거나(attack, 방패) 피하고, 체력 6 이하로 몰렸을 때만 night_shelter로 잠깐 숨는다. 지하로 내려가는 건 목표(광석 등)에 필요할 때만. 밤에 쉬거나 지하로 피하라는 예전 교훈은 따르지 않는다.
- 광석이 근처에 없으면: 광석 높이로 descend_mine, 동굴이 보이면 explore_cave, 지표면은 explore_area.
- 지하에서 나무(원목)가 필요하면 go_to_surface로 먼저 올라간다. 도구 내구도가 낮으면 부서지기 전에 새 도구 재료를 챙긴다.
- '자동 계획'은 게임 데이터로 계산한 정답 순서다. 위급한 생존 상황이 아니면 follow_plan으로 여러 단계를 한 번에 실행하는 것이 가장 빠르다 (또는 '추천 다음 행동' 하나).
- "skill"에는 '보유 스킬' 목록에 실제로 있는 이름만 쓴다. 실패해서 저장되지 않은 스킬 이름은 존재하지 않는다.
- 현재 목표를 한 단계씩 진행. 이미 있는 스킬로 되면 그걸 쓴다 (원목은 collect_logs, 제작은 craft_item, 설치는 place_block). 맞는 스킬이 없거나 연속 실패 중이면 write_skill.
- 직전 결과의 에러 메시지를 읽고 원인을 해결하는 방향으로 다음 행동을 고른다. 같은 실패를 반복하지 않는다.
- '교훈' 목록은 과거 실수다. 반복하지 않는다.

${API_DOC}`;

export const WRITER_SYSTEM = `너는 마인크래프트 봇(mineflayer)의 스킬 코드를 작성하는 개발자다. 요청받은 스킬을 JavaScript 함수 본문으로 작성한다.

${API_DOC}

출력 형식 (정확히 이 형식, 다른 설명 금지):
NAME: snake_case_이름 (3~40자, 소문자/숫자/_)
DESCRIPTION: 한 줄 설명 (한국어)
ARGS: 테스트용 기본 인자 JSON 한 줄. 예: {"count": 3}
CODE:
\`\`\`js
// async 함수 본문. api, args 사용. return 으로 결과 반환
\`\`\`

원칙: 기본 스킬(collect_logs, craft_item, place_block, mine_block, smelt_item, eat_food, explore_area 등)은 수정 불가이고 이미 잘 동작한다 - 그 복사본(smelt_item_v2 같은)이나 확인만 하는 스킬(check_..., verify_...)은 만들지 않는다. 기본 스킬을 조합해야 할 때만 새 이름으로 만들고 api.useSkill로 호출한다. api 호출은 Promise.all 등으로 동시에 돌리지 말고 하나씩 await 한다. 작게 만들고, 실패 가능한 곳은 에러 메시지가 명확하도록 둔다 (try/catch로 삼키지 말 것). 작은 스킬을 조합하는 상위 스킬은 코드 안에서 await api.* 를 직접 호출해 구성한다.`;

export function parseWriterReply(text) {
  const name = text.match(/^NAME:\s*([a-z][a-z0-9_]*)/m)?.[1];
  const description = text.match(/^DESCRIPTION:\s*(.+)$/m)?.[1]?.trim();
  const argsLine = text.match(/^ARGS:\s*(.+)$/m)?.[1];
  const code = text.match(/```(?:js|javascript)?\n([\s\S]*?)```/)?.[1]?.trim();
  if (!name || !description || !code) throw new Error('writer reply missing NAME/DESCRIPTION/CODE');
  let args = {};
  try {
    args = argsLine ? JSON.parse(argsLine) : {};
  } catch {}
  return { name, description, args, code };
}

export const REFLECT_SYSTEM = `너는 마인크래프트 봇의 코치다. 최근 일지와 기존 교훈을 보고, 앞으로 같은 실수를 막을 "교훈"을 최대 8개로 다시 정리한다.
규칙: 구체적이고 행동 가능한 한 줄 (예: "용암이 보이면 descend_mine 방향을 바꾼다"). 중복/모호한 것은 합치거나 버린다. 아래 JSON 하나만 출력:
{"lessons": ["...", "..."]}`;

export const GOAL_SYSTEM = `너는 마인크래프트 생존 플레이어다. 기본 도구는 갖췄다. 지금 상황을 보고 다음에 할 일 하나를 스스로 정한다.
사람 플레이어처럼 생각한다: 위험하면 안전 먼저(갑옷, 음식, 침대), 여유가 있으면 발전(다이아몬드, 철 갑옷, 방패, 농장, 집, 탐험).
최근에 포기한 목표는 다시 고르지 않는다. 너무 큰 목표 말고 몇십 턴 안에 끝낼 수 있는 크기로.
인벤토리 아이템으로 확인할 수 있는 목표면 item(마인크래프트 영문 ID)과 count를 정한다. 확인할 아이템이 없는 목표면 item은 null.
JSON 하나만 출력: {"goal": "한 줄 설명(한국어)", "item": "iron_chestplate", "count": 1}`;
