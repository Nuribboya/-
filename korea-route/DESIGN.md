# 제품 방향 설계: 한국 여행 "지금 여기서 다음엔 어디로"

작성: 2026-10. 경쟁 서비스와 공개 보도 자료를 조사해서 정리한 것이다. 출처는 맨 아래에 있다.

## 1. 결론

**여행 전에 일정을 짜주는 시장은 이미 포화다. 여행 중에 "지금 여기서, 지금 시간에, 남은 곳을 어떻게 돌고 어디서 먹을지"를
한국 특유의 제약(브레이크타임, 정기휴무, 웨이팅, 1인분 주문 불가, 도시 간 열차)까지 반영해서 바로 답해주는 쪽으로 간다.**

- 타깃: 한국에 온 외국인 자유여행자 (영어 우선, 한국어도 지원)
- 한 줄 소개: *"Korea-aware re-planner: what's next from where you are, right now."*
- 수익: 광고보다 제휴(투어, 교통패스, 식당 예약) 위주. 광고는 보조.

## 2. 경쟁 서비스 분석

| 분류 | 대표 서비스 | 잘하는 것 | 빈틈 |
| --- | --- | --- | --- |
| 국내 지도 | 네이버 지도, 카카오맵 | 한국 장소 데이터 최강. 네이버는 리뷰 번역(영/중/일), "BE LOCAL" 현지인 저장 탭(서울→부산·경주 확대) | 여러 곳 방문 순서 최적화 없음. 외국인은 영어 주소/키워드 검색이 여전히 불편 |
| 글로벌 지도 | 구글 지도 (+ Gemini "Ask Maps") | 2026.2 한국 고정밀 지도 반출 조건부 허가. Ask Maps는 여러 곳 일정 생성까지 함 (2026.3 미국·인도 출시) | 한국 반영은 국내 서버 처리·승인 절차가 남아 시점 불확실. 한국 식당 특유의 제약(브레이크타임, 1인분 불가)은 약함 |
| 일정 플래너 | Wanderlog, 마이로(MYRO), VisitKorea 플래너 | 장소 담기 → 순서 최적화. 마이로는 영업시간·식사시간까지 고려 | Wanderlog 최적화는 유료(연 약 $40). 마이로는 해외 여행지 중심. 대부분 "여행 전" 계획용이라 현장에서 다시 짜기 약함 |
| AI 플래너 | ChatGPT, Mindtrip, Layla, Creatrip AI, Visit Seoul 챗봇 | 대화로 그럴듯한 일정 | 정확도 문제 (ChatGPT 일정 90%에 오류: 폐업, 틀린 영업시간, 불가능한 동선). 대화가 느림 |
| 식당 | 캐치테이블(영문판, 이메일 가입), 네이버 예약, 테이블링 | 예약·웨이팅 | 동선과 따로 놂. "이 근처에서 지금 열려 있고 혼자 갈 수 있는 곳"을 동선 안에서 못 찾음 |

### 핵심 관찰

1. **"순서 최적화"만으로는 차별화가 안 된다.** Wanderlog, 마이로, VisitKorea, 구글 Ask Maps가 다 하거나 하려 한다.
2. **"구글 지도가 한국에서 안 된다"는 기회는 줄어든다.** 2026.2 허가 이후 1~2년 안에 구글이 걸음/길찾기를 채울 가능성이 높다.
3. **아무도 제대로 못 하는 건 "한국 특유의 시간 제약"과 "현장 재계획"이다.**
   - 식당 브레이크타임(보통 15~17시), 월요일 등 정기휴무, 라스트오더
   - 1인 방문 불가 / 2인분 이상 주문 (혼자 여행하는 외국인이 자주 겪음)
   - 유명 맛집 웨이팅 1시간 이상
   - 도시 간 이동은 KTX/시외버스 시간표에 묶임 (인천공항→부산 직행 열차 없음, AREX 급행은 T-money 불가 등)
4. **AI 일정은 정확도가 약점이다.** 우리는 "계산된 이동시간 + 검증된 영업 정보"로 정확도를 무기로 삼는다.

## 3. 수요 판단 (높은 순)

| 순위 | 불편 | 근거 | 우리가 풀 수 있나 |
| --- | --- | --- | --- |
| 1 | 현지 앱 언어/검색 불편 | 외국인 대상 조사·보도에서 반복 | 부분적으로 (영문 이름, 로마자, 한글 병기). 네이버가 빠르게 개선 중이라 단독 무기로는 약함 |
| 2 | 대중교통·도시 간 이동이 복잡 | 관광공사 불편신고센터 민원의 다수가 대중교통 관련 | 예 (구간별 이동 + 도시 간 열차 구간 안내) |
| 3 | 계획이 틀어졌을 때 다시 짜기 | AI/플래너 리뷰의 공통 불만, 직접 겪은 불편 | **예, 핵심 기능** |
| 4 | 맛집 갔더니 브레이크타임/휴무/혼밥 불가 | 한국 식당 구조상 반복 발생 | **예, 차별화 포인트** |
| 5 | 현지인이 가는 곳 찾기 | 네이버 BE LOCAL이 이 수요를 겨냥 | 직접 경쟁은 비추. 큐레이션 데이터로 보조 |

## 4. 제품 설계

### 4.1 핵심 화면은 하나: "Now" 화면

여행 중에 열면 바로 이 화면이다.

```
[현재 위치 · 오후 2:40 · 비 옴]
남은 곳: 감천문화마을, 자갈치시장, 광안리(야경)

추천 순서
 1. 감천문화마을   🚇 25분   (18시 이후엔 상점 대부분 닫음 → 지금 가야 함)
 2. 🍜 점심/간식: 남포동 OO국밥  🚶 8분  (15:00~17:00 브레이크타임 → 14:55 전 도착 불가, 대신 △△ 추천)
 3. 자갈치시장     🚶 5분
 4. 광안리        🚇 35분  (야경 → 19시 이후 도착하도록 배치)

[다시 짜기]  [이곳 건너뛰기]  [여기서 30분 더 있기]
```

- 버튼 세 개("다시 짜기", "건너뛰기", "더 있기")가 핵심. 누를 때마다 남은 일정을 즉시 재계산 (1초 이내, AI 대화 없이).
- 각 장소에 **시간 제약 경고**: 브레이크타임, 휴무, 마감, 야경/일출 같은 "좋은 시간대".

### 4.2 기능 단계

**1단계 (지금 ~ 2주): 시간을 아는 최적화**
- 장소마다 `영업시간`, `휴무일`, `브레이크타임`, `추천 체류시간`, `좋은 시간대` 필드 추가
- 최적화 기준을 "총 이동시간 최소"에서 "**제약을 지키면서** 총 시간 최소"로 변경
  (시간창이 있는 순회 문제. 장소 15곳 이하라 지금의 동적계획법을 확장하면 충분히 빠름)
- "더 있기 / 건너뛰기" 버튼
- 부산 핵심 장소 50곳 + 식당 50곳 데이터를 직접 정리 (한국관광공사 TourAPI에서 시작해 검수)

**2단계 (1~2개월): 식사 끼워 넣기**
- "점심을 12~14시 사이에 넣어줘" → 동선 위에서 그 시간에 열려 있는 식당 후보를 자동 추천
- 필터: 혼밥 가능, 채식, 웨이팅 짧음, 영어 메뉴
- 식당마다 캐치테이블/네이버 예약 링크 (제휴 가능 시 수수료)

**3단계 (2~3개월): 도시 간 이동**
- "부산 2일 → 경주 1일 → 서울" 같은 큰 그림을 먼저 짜고, 도시 안 동선으로 내려가는 2단 구조
- KTX/SRT/시외버스는 실시간 대신 "대표 소요시간 + 배차 간격" 정적 표로 시작
- 코레일패스, 교통카드(T-money/기후동행카드) 사용 가능 여부 안내

**나중에 (반응을 보고)**
- 날씨 연동 (비 오면 실내 위주로 재배치)
- 서울, 제주, 경주로 지역 확장
- 대중교통 실시간 경로 API (ODsay 등, 유료) 연결

### 4.3 일부러 안 하는 것

- **AI 대화형 플래너**: 큰 회사들이 이미 잘하고 비용이 든다. 우리는 "버튼 한 번에 계산된 답"으로 간다.
- **자체 리뷰/평점**: 네이버·구글과 정면 승부가 된다. 대신 검수한 소수 데이터와 외부 링크로 간다.
- **전국 커버리지**: 부산부터 깊게. 데이터 품질이 이 서비스의 무기다.

## 5. 데이터 소스

| 데이터 | 소스 | 비용 | 비고 |
| --- | --- | --- | --- |
| 관광지/식당 기본 정보, 영문명, 이용시간, 휴무일 | 한국관광공사 TourAPI (국문/영문 서비스) | 무료 (키 필요) | 이용시간이 자유 텍스트라 사람이 한 번 정리해야 함 |
| 가게 검색 | 카카오 로컬 (적용 완료) | 무료 한도 넉넉 | 영업시간은 안 줌 |
| 영문명, 지도 | OpenStreetMap (적용 완료) | 무료 | 작은 가게 영문명 부족 |
| 브레이크타임, 혼밥 가능 여부 | 직접 조사 + 사용자 제보 | 시간 | 이게 해자(진입장벽)가 됨 |
| 도시 간 열차/버스 | 코레일·버스 공개 시간표로 정적 표 작성 | 무료 | 실시간 아님 |

## 6. 수익 모델

1. **제휴 수수료 (주력)**: Klook·KKday 투어/입장권, 코레일패스, 유심/eSIM, 숙소. 동선 안의 장소에 "예약하기"를 자연스럽게 붙인다.
2. **식당 예약 연계**: 캐치테이블 등과 제휴가 되면 예약당 수수료.
3. **광고 (보조)**: 계획 화면 아래쪽에만. 여행 중 "Now" 화면에는 넣지 않는다 (사용성이 무너짐).
4. 나중에: 지역 관광재단·지자체 협업 (부산관광공사 등은 외국인 동선 데이터에 관심이 큼).

## 7. 위험과 대응

| 위험 | 가능성 | 대응 |
| --- | --- | --- |
| 구글 지도가 한국에서 완전해지고 Ask Maps가 들어옴 | 높음 (1~2년) | 구글이 약한 한국 식당 제약 데이터와 현장 재계획 속도로 차별화. 구글 길찾기 링크는 오히려 활용 |
| 네이버가 순서 최적화/영문을 강화 | 중간 | 외국인 전용 경험과 도시 간 2단 일정은 네이버의 우선순위가 아님 |
| 직접 정리한 데이터가 낡음 | 높음 | 지역을 좁히고, "정보가 틀렸어요" 제보 버튼으로 갱신 |
| 무료 공개 서버(OSRM, Photon, CARTO) 제한 | 트래픽 늘면 확실 | 상업화 전에 자체 서버 또는 유료 플랜으로 전환 |

## 8. 바로 다음 작업

1. 장소 데이터에 시간 필드 추가 (`open`, `close`, `breakStart`, `breakEnd`, `closedDays`, `stayMin`, `bestTime`)
2. 최적화를 시간창 지원 버전으로 확장 (불가능한 일정이면 어떤 제약 때문인지 알려주기)
3. 부산 핵심 50곳 + 식당 50곳 데이터 정리
4. "Now" 화면과 "건너뛰기 / 더 있기" 버튼
5. 실제 부산 여행에서 직접 써보고 고치기

## 출처

- 외국인의 국내 지도 앱 불편: [Korea Times (2025)](https://www.koreatimes.co.kr/amp/business/tech-science/20250418/local-maps-gain-popularity-but-leave-foreigners-unsatisfied), [Korea Stripes](https://korea.stripes.com/travel/apps-foreign-traveler.html), [Asia News Network](https://asianews.network/?p=200222)
- 구글 지도 데이터 반출 조건부 허가 (2026.2.27): [TechCrunch](https://techcrunch.com/2026/02/27/south-korea-opens-the-door-to-let-google-maps-operate-fully), [AA](https://aa.com.tr/en/asia-pacific/south-korea-allows-google-to-export-map-data/3842473)
- 구글 Ask Maps: [Travolution](https://www.travolution.com/news/google-maps-launches-ask-maps-and-immersive-navigation-with-gemini/), [PCWorld](https://www.pcworld.com/article/3086987/gemini-just-became-your-new-google-maps-buddy.html)
- 네이버 리뷰 번역, BE LOCAL: [Naver 보도자료](https://www.navercorp.com/media/pressReleasesDetail?seq=32463), [Korea Times (2025.9)](https://www.koreatimes.co.kr/amp/business/companies/20250917/naver-map-launches-another-be-local-campaign-expands-to-busan-gyeongju)
- Wanderlog 최적화 유료: [App Store](https://apps.apple.com/us/app/wanderlog/id1476732439), [MonkeyTravel 비교 (경쟁사 작성)](https://monkeytravel.app/blog/wanderlog-vs-mindtrip-2026)
- 마이로: [벤처스퀘어](https://www.venturesquare.net/874198/), [머니투데이](https://www.mt.co.kr/future/2024/09/03/2024082308001722832)
- AI 일정 정확도: [Rus Tourism News (SEO Travel 조사 인용)](https://www.rustourismnews.com/90-of-ai-travel-itineraries-are-inaccurate/), [Layla Trustpilot](https://www.trustpilot.com/review/layla.ai?page=4)
- 관광공사 민원·외국인 수: [JoongAng Daily](https://www.koreajoongangdaily.com/lifestyle/korea-tourism-organization-heeds-complaints-of-foreign-travelers/11150306), [Khan (2026.1)](https://sports.khan.co.kr/en/article/202601192356447)
- VisitKorea 플래너, Creatrip AI: [JoongAng Daily](https://www.koreajoongangdaily.com/lifestyle/visitkorea-launches-travel-itinerary-app-for-travelers-to-korea/10860371), [Khan (2025.12)](https://sports.khan.co.kr/en/article/202512162251317)
- 캐치테이블 영문판: [KED Global](https://amp.kedglobal.com/newsAmp/ked202404170003), [JoongAng Daily](https://www.koreajoongangdaily.com/korea/kto-partners-with-catch-table-in-effort-to-demystify-korean-menus/11306538)
- 도시 간 이동 (인천공항→부산, AREX): [Wise](https://wise.com/ph/blog/korea-itinerary), [Klook](https://www-verify.klook.cn/en-PH/blog/korea-rail-pass-seoul-busan)
