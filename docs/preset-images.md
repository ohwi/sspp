# 프리셋에서 온라인 원본 이미지 확보

공유 코드는 캐릭터 ID와 잠재력 목표치를 담는다. 이미지 픽셀이나 이미지 URL은 들어 있지 않다. 코드에서 복원한 잠재력 ID를 공개 게임 데이터의 `Item[id].Icon`과 연결하면 원본 이미지 경로를 찾을 수 있다.

## 확인한 공개 자료

- [SSToy 원본 아이콘](https://github.com/JforPlay/sstoy/tree/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/public/assets/skill_icons): `Item.Icon`의 마지막 파일명에 `_A.png`를 붙인 원본 glyph. `Potential.Corner`의 원·삼각형·마름모 표식도 제공한다.
- [SSToy Item 데이터](https://github.com/JforPlay/sstoy/blob/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/public/data/Item.json): 잠재력 ID, 원본 그림 경로, 종류와 희귀도 연결.
- [SSToy Potential 데이터](https://github.com/JforPlay/sstoy/blob/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/public/data/Potential.json): 잠재력의 캐릭터 소속과 구분 도형.
- [게임 잠재력 아이콘 컨트롤러](https://github.com/MakoStar/StellaSoraData/blob/69b00a26d9efb2846a25b92df0e87e95a4fd00a4/_Lua/Game/UI/StarTower/Potential/PotentialIconCtrl.lua#L7): 아이콘 `_A`, Corner 도형, 희귀도 색의 게임 내 연결.

고정 커밋은 SSToy `34f57606015277cf9da76a2c7cc14fa1b68ccbbf`와 게임 Lua `69b00a26d9efb2846a25b92df0e87e95a4fd00a4`를 사용했다. 원본 그림은 게임 저작물로, 저장소 코드의 MIT 라이선스 대상이 아니다.

## 사용자 코드의 누락 이미지

`AAAAiwAAAKAAAACCMAbDZDDNlgAABs2AQDwA`의 선택 목표 22개 중 기존 인식 템플릿이 없는 20개에 대응하는 원본 glyph 14개와 엘리·토나 얼굴 원본을 확보했다. 내려받은 16개 파일은 고정 Git tree의 blob 해시와 일치했다. 같은 glyph를 여러 희귀도·도형의 잠재력이 공유하므로, 원본 14개와 인식 템플릿 20개는 개수가 다르다.

| 캐릭터 ID | 잠재력 ID | 한국어 이름 | 원본 그림 |
| --- | --- | --- | --- |
| 139 | 513903 | 먼지 제거 소용돌이 | `13901_Potential_03_A.png` |
| 139 | 513904 | 청소의 연속 | `13901_Potential_04_A.png` |
| 139 | 513908 | 칼날 휩쓸기 | `13901_Potential_04_A.png` |
| 139 | 513909 | 청소 대기 지점 | `13901_Potential_03_A.png` |
| 139 | 513913 | 빗자루 휩쓸기 | `Common_Potential_15_A.png` |
| 139 | 513910 | 추가 작업 | `13901_Potential_04_A.png` |
| 139 | 513912 | 중복 청소 | `Common_Potential_Water_1_A.png` |
| 139 | 513942 | 빠른 먼지 제거 | `13901_Potential_42_A.png` |
| 160 | 516025 | 여전한 훈계 | `16001_Potential_21_A.png` |
| 160 | 516026 | 낮게 부는 바닷바람 | `16001_Potential_21_A.png` |
| 160 | 516027 | 멈추지 않는 바다 소리 | `16001_Potential_22_A.png` |
| 160 | 516028 | 도시를 삼키는 파도 | `16001_Potential_23_A.png` |
| 160 | 516043 | 해풍의 규율 | `16001_Potential_41_A.png` |
| 130 | 513021 | 시위를 떠난 화살 | `13001_Potential_21_A.png` |
| 130 | 513022 | 남겨진 화살의 매듭 | `13001_Potential_22_A.png` |
| 130 | 513025 | 화살의 추격 | `13001_Potential_25_A.png` |
| 130 | 513026 | 서리 맺힌 화살 | `13001_Potential_22_A.png` |
| 130 | 513030 | 차가운 파동의 전조 | `13001_Potential_23_A.png` |
| 130 | 513029 | 거세지는 물길 | `13001_Potential_29_A.png` |
| 130 | 513033 | 잔잔한 물결 | `Common_Potential_15_A.png` |

## 실제 화면과의 비교

2026-10-12 한국어 클라이언트의 프리셋 미리보기 화면을 읽기 전용으로 캡처했다. 엘리의 `513903` 큰 상세 카드에 표시된 흰 glyph와 온라인 원본 `13901_Potential_03_A.png`의 alpha 실루엣은 크기와 위치를 맞췄을 때 정규화 상관계수 약 0.950이었다. 원본 그림의 동일성을 확인한 결과이며, 전체 카드 템플릿 검증과는 구분한다.

작은 프리셋 목록의 윌로 `516021`·`516022`는 기존 선택 화면의 150px 템플릿을 각각 92px·91px로 줄인 패치와 상관계수 0.931·0.903으로 일치했다. 대조 그림 `516023`은 0.225·0.264였다. 하지만 작은 카드의 그림 영역 전체를 150px로 바꾸면 0.256·0.099에 그쳤고, glyph를 뺀 배경 비교는 0.421·0.348이었다. 작은 목록 전체를 그대로 리사이즈하여 등록하는 방법을 검증한 것은 아니다.

## 온라인 원본 합성 시험

게임용 배경 원본 `rare_vestige_card_s_4.png`(금색), `_5.png`(보라), `_6.png`(핑크)도 확보했다. 세 파일은 각각 190×242 RGBA이며 위 SSToy 고정 커밋의 Git blob과 일치했다. 목록 배경에 대한 Lua 참조와 시각 비교는 확인했지만, 상세 카드 prefab의 고정 배경 설정 전체는 공개되지 않았다.

핑크 `_6` 배경과 윌로 원본 glyph 세 개에 **같은 배경 변환, glyph 배율 0.59, 중심 (83, 75), 출력 150×150**을 적용한 시험 이미지가 실제 선택 화면에서 다음 점수를 보였다. 같은 캐릭터의 다른 두 시험 이미지로 비교한 점수는 모두 0.258 이하였다.

| 잠재력 ID | 온라인 원본 합성 이미지의 상관계수 |
| --- | ---: |
| 516021 | 0.948 |
| 516022 | 0.945 |
| 516023 | 0.932 |

이는 원본에서 인식 후보를 만드는 경로가 작동한다는 근거다. 한 한국어 1600×900 선택 화면을 1280×720로 정규화했고, 같은 화면으로 공통 기하학을 맞추고 평가했다. 독립 화면 검증은 아니다. 전용 핑크 카드 세 개만 시험했으며 일반 금색·보라색 카드, Corner 도형 구분, 신규 얼굴, 다른 화면 크기의 인식을 검증한 결과로 확대하지 않는다. 시험 후보는 production 템플릿에 등록하지 않았다.

## ID별 완성 카드 DB

[AutumnVN/ssassets 고정 자료](https://github.com/AutumnVN/ssassets/tree/a970adc8ae875ffbfbad98a138ba569402959b4b/potential)에는 `potential/{잠재력 ID}.webp` 형태의 180×230 카드 이미지가 있다. [이 DB의 이미지 구성 코드](https://github.com/AutumnVN/ssassets/blob/a970adc8ae875ffbfbad98a138ba569402959b4b/sharp.js)는 잠재력 ID, 원본 glyph, 배경, Corner와 영문 이름을 연결한다. 원본 PNG와 구분되는 합성 카드 자료이며, 그 코드를 앱에 복사하거나 실행하지 않았다.

윌로 `516021`·`516022`·`516023`의 중심 90×90 영역을 크기별로 대조하면 저장된 한국어 선택 화면에서 0.976·0.965·0.962로 일치했다. 다른 두 그림의 비교 점수는 최대 0.481이었다. 전체 영문 카드는 화면 배치와 크기가 달라 그대로는 맞지 않으며, 중심 영역만 사용하면 Corner를 버리므로 같은 그림을 쓰는 다른 잠재력을 구분할 수 있다는 근거로 사용하지 않는다. 이 결과는 중앙 그림의 동일성을 확인한 것이며 신규 완성 인식 템플릿의 등록 검증은 아니다.

추가로 앱과 같은 ROI 단위 정규화에서 위 SSToy 공통 합성 후보의 점수는 `516021=0.958`, `516022=0.949`, `516023=0.935`였다. 대조 후보 점수는 최대 0.252였다. 같은 저장 화면을 사용한 핑크 전용 카드 세 개라는 검증 범위는 유지한다.

사용자 프리셋의 누락 20개와 검증용 윌로 3개, 총 23개 카드를 내려받아 Git blob 및 SHA256을 모두 대조했다. 원본 URL의 확장자는 `.webp`이지만 실제 바이트 형식은 PNG여서, 로컬 파일명만 `{ID}.png`로 바꾸고 이미지 내용은 유지했다. 각 파일의 원본 URL, ID·캐릭터·한국어 이름, 해시와 목표값은 [출처 기록](preset-card-sources.json)에 있다. 잠재력 ID 연결은 [ss-data 고정 자료](https://github.com/AutumnVN/ss-data/blob/4eb1ff7e08c2e9576c44bdaa4380c63da053f726/character.json)와도 대조했다. 엘리·토나·윌로(여름)의 ID별 29개 카드가 원본 tree에 각각 모두 있다.

## 인식 템플릿으로 구성할 때 필요한 작업

현재 앱은 회색조의 150×150 템플릿을 선택 화면에서 찾는다. 온라인 glyph PNG는 투명 배경이고 그림마다 잘린 크기가 다르다. 배경·희귀도·Corner 표식을 함께 구성하고, 게임에서의 그림 크기·중심·잘림을 맞춰야 한다. 같은 그림을 쓰는 다른 잠재력을 구분하는지도 확인해야 한다.

인터넷 원본 확보와 앱 인식 등록은 별도 단계다. 이번 조사 원본은 앱의 production 템플릿에 추가하지 않았다. 기존에 확인한 윌로 선택 화면 카드 3개의 인식 범위는 유지한다.

추가 화면이 필요하면 [프리셋 상세 카드 코드](https://github.com/MakoStar/StellaSoraData/blob/69b00a26d9efb2846a25b92df0e87e95a4fd00a4/_Lua/Game/UI/PotentialPreselection/potentialpreselectioneditctrl.lua#L344)와 [탑 선택 카드 코드](https://github.com/MakoStar/StellaSoraData/blob/69b00a26d9efb2846a25b92df0e87e95a4fd00a4/_Lua/Game/UI/StarTower/Potential/PotentialSelectCtrl.lua#L254)가 같은 `PotentialCardItemCtrl`을 사용한다. 프리셋 편집에서 목표 0인 잠재력도 상세를 열 수 있으므로, 탑의 무작위 카드 출현을 기다리지 않고 참조 화면을 확보할 경로가 있다. 공개된 prefab 크기가 없어 두 화면의 완전한 픽셀 동일성은 아직 확인하지 못했다.
