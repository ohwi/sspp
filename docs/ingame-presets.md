# 게임 내 잠재력 프리셋 코드

게임의 **잠재력 프리셋 공유 코드**에는 주력 캐릭터 한 명, 지원 캐릭터 두 명과 각 잠재력의 목표치가 들어 있다. SSToy 빌드 URL과 별도의 형식이며, 목표 Lv2·Lv3·Lv6을 그대로 보존해야 한다. 가져온 목표치는 현재 획득한 레벨을 뜻하지 않는다.

## 자료와 카탈로그

캐릭터와 잠재력 순서는 [JforPlay/sstoy의 고정 커밋](https://github.com/JforPlay/sstoy/tree/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/public/data) `34f57606015277cf9da76a2c7cc14fa1b68ccbbf`에서 가져온다. 이 커밋에는 `Config.json`이 없어 목표치 상한은 [Hiro420/StellaSoraData의 KR Config](https://github.com/Hiro420/StellaSoraData/blob/225c0b76027f7345a712a2d62dbe7ee22de2f836/KR/bin/Config.json) `225c0b76027f7345a712a2d62dbe7ee22de2f836`의 `PotentialPreselectionMaxLevel.Value = "6"`을 사용한다. 두 저장소는 공개된 게임 데이터 자료이며, 이 파일만으로 현재 실행 중인 클라이언트의 모든 화면 동작을 검증한 것은 아니다.

`app/resources/characters.json`은 기존 `schema_version: 1`과 이미지 폴더용 `key`를 유지하며 다음 자료를 추가한다.

| 필드 | 의미 |
| --- | --- |
| `ingame_preset_max_level` | 일반·공용 잠재력의 게임 프리셋 목표 상한. 이 자료에서는 `6`. |
| `source.sha256` | SSToy에서 수집한 각 원본 파일의 SHA256. |
| `source.ingame_preset_config` | 별도 Config의 저장소, 고정 커밋, 경로, 원본 URL 및 SHA256. |
| `characters[id].preset_schema` | 아래 다섯 배열. 원본 순서를 보존한 정수 잠재력 ID. |
| `characters[id].potential_names` | 문자열 잠재력 ID → 정확한 한국어 게임 명칭. |
| `characters[id].potentials` | 기존 잠재력 ID → `Potential.MaxLevel` 메타데이터. 변경하지 않는다. |

한국어 잠재력 이름은 [Item.json](https://github.com/JforPlay/sstoy/blob/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/public/data/Item.json)의 `Item[id].Title`을 [KR/Item.json](https://github.com/JforPlay/sstoy/blob/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/public/data/KR/Item.json)에 대조한다. `Potential.json`에는 `Name` 필드가 없으며, `KR/Potential.json`은 효과 설명의 번역이므로 이름으로 사용하지 않는다.

## 캐릭터와 잠재력의 순서

[CharPotential.json](https://github.com/JforPlay/sstoy/blob/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/public/data/CharPotential.json)의 배열은 다음과 같다.

| 원본 필드 | 개수 | 사용 슬롯 | 값당 비트 수 |
| --- | ---: | --- | ---: |
| `MasterSpecificPotentialIds` | 4 | 주력 전용 | 1 |
| `MasterNormalPotentialIds` | 9 | 주력 일반 | 3 |
| `AssistSpecificPotentialIds` | 4 | 지원 전용 | 1 |
| `AssistNormalPotentialIds` | 9 | 지원 일반 | 3 |
| `CommonPotentialIds` | 3 | 주력·지원 공용 | 3 |

현재 카탈로그의 `Visible`·`Available` 캐릭터 40명 모두 이 배열 크기를 만족하며, 다섯 배열의 합집합은 캐릭터마다 잠재력 29개다. 슬롯 하나가 저장하는 목표치는 해당 역할의 전용 4개, 일반 9개, 공용 3개를 합친 **16개**다. 전체 29개를 한 슬롯에 순서대로 읽는 형식이 아니다.

각 배열은 숫자순으로 정렬하면 안 된다. 예를 들어 원본의 일반 잠재력 순서가 `...513909, 513913, 513910...`일 때 숫자순으로 바꾸면 공유 코드의 목표치가 다른 잠재력에 배정된다. 카탈로그 생성기는 다섯 배열의 개수, 양의 정수 ID, 배열 내 중복 여부, 합집합 29개 및 잠재력의 캐릭터 소속을 검사한다.

## 공유 코드 형식과 목표 레벨

[SSToy의 게임 코드 codec](https://github.com/JforPlay/sstoy/blob/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/src/modules/app-ingame-codec.ts)을 기준으로 읽는다.

1. 표준 Base64 코드를 바이트로 해제한다.
2. 앞 12바이트를 big-endian uint32 세 개로 읽는다. 순서는 `[주력, 지원1, 지원2]`이다.
3. 이후 각 슬롯에서 역할에 맞는 전용 → 일반 → 공용 배열의 원래 순서를 따라 MSB부터 비트를 읽는다.
4. 전용 잠재력은 1비트 선택 값 `0/1`, 일반·공용 잠재력은 3비트 목표치 `0..6`이다. `0`은 선택하지 않은 목표다. 3비트에 표현 가능한 값 `7`은 이 자료의 목표 상한을 넘는다.

이 자료의 세 캐릭터 슬롯을 모두 포함하는 코드는 슬롯마다 `4×1 + 9×3 + 3×3 = 40`비트가 필요하다. 헤더 96비트와 본문 120비트를 합친 216비트, 즉 27바이트다.

`Potential.MaxLevel`은 이 원자료에서 전용 잠재력은 `1`, 일반·공용 잠재력은 `3`이다. 이 값을 게임 프리셋 목표 상한으로 사용하면 유효한 Lv4·Lv5·Lv6을 잘못 거부한다. 목표 상한은 Config의 `6`으로 읽고, 저장된 목표치에 `×2` 같은 변환을 적용하지 않는다. 기존 `potentials`의 `MaxLevel` 메타데이터는 호환성을 위해 그대로 보존한다.

## 실제 코드 확인

사용자가 제공한 코드:

```text
AAAAiwAAAKAAAACCMAbDZDDNlgAABs2AQDwA
```

독립적인 Base64·big-endian·비트 파싱 결과는 27바이트이며 캐릭터 순서는 `[139, 160, 130]`이다. 아래는 0보다 큰 목표치 전부이고, 각 역할의 나머지 잠재력은 목표 `0`이다.

| 슬롯 | 캐릭터 | 목표 1 | 목표 2 | 목표 3 | 목표 6 |
| --- | --- | --- | --- | --- | --- |
| 주력 | 엘리 (`139`) | `513903`, `513904` | `513912` | — | `513908`, `513909`, `513910`, `513913`, `513942` |
| 지원1 | 윌로(여름) (`160`) | `516021`, `516022` | `516027` | — | `516025`, `516026`, `516028`, `516043` |
| 지원2 | 토나 (`130`) | `513021`, `513022` | `513030` | `513029` | `513025`, `513026`, `513033` |

이 코드에서 Lv2는 엘리의 **중복 청소** (`513912`), 윌로(여름)의 **멈추지 않는 바다 소리** (`516027`), 토나의 **차가운 파동의 전조** (`513030`)다. 토나의 **거세지는 물길** (`513029`)은 Lv3이다. 이 목표치를 예전의 Lv1/Lv6 두 단계 우선순위로 바꾸면 사용자가 저장한 값이 사라진다.

외부 예시는 [게임 데이터의 PotentialPreset.json](https://github.com/Hiro420/StellaSoraData/blob/225c0b76027f7345a712a2d62dbe7ee22de2f836/KR/bin/PotentialPreset.json)에도 있다.

| 원본 프리셋 ID | 공유 코드 | 캐릭터 순서 |
| --- | --- | --- |
| `10301` | `AAAAZwAAAGwAAAB+MAXE4gBRsQAMAKxwAGBA` | 코하쿠 (`103`), 카시미라 (`108`), 플로라 (`126`) |
| `10302` | `AAAAZwAAAGwAAAB+zbAEggBdgQBgAMGwDAEA` | 코하쿠 (`103`), 카시미라 (`108`), 플로라 (`126`) |

첫 예시에는 `510308`의 목표 Lv5가, 둘째 예시에는 `512641`의 목표 Lv4가 포함되어 있다. 해당 테이블의 코드도 본문 120비트를 소비하며 추가 패딩 비트가 없다.

## 인식 범위

### 목표 해석과 표시

원본 목표치는 저장·다시 읽기·인식 결과 전달 과정에서 그대로 보존한다. 이 브랜치의 표시 해석은 사용자가 정한 다음 규칙을 따른다.

| 프리셋 값 | 카드 표시 | 의미 |
| --- | --- | --- |
| 0 | 목표 제외 | 선택할 목표에서 제외 |
| 1~2 | Lv.1 확보 | 한 번 확보하면 목표 충족 |
| 3~5 | 다다익선 | 추가 획득을 계속 선호 |
| 6 | 목표 Lv.6 | Lv.6을 목표로 획득 |

가져오기 설명에는 `Lv.1 확보 (프리셋 Lv.2)` 또는 `다다익선 (프리셋 Lv.3)`처럼 원래 값을 함께 표시한다. 게임 목표값 자체를 바꾸거나 기존 SSToy 우선순위에 이 규칙을 적용하지 않는다. 현재 레벨을 읽지 않으므로, Lv.1 또는 Lv.6 달성 이후 카드 표시를 자동으로 끄는 기능은 아직 없다.

### 이미지 준비 상태

코드에서 캐릭터와 목표를 복원할 수 있어도 해당 캐릭터의 화면 인식 이미지가 모두 준비되었다는 뜻은 아니다. 캐릭터 40명의 메타데이터 범위와 이미지 템플릿 범위는 별도로 판단한다. 누락된 잠재력 이미지는 기존 템플릿 감사와 앱의 누락 안내로 확인해야 한다.

앱은 카드 이미지를 정적 템플릿으로 인식한다. 목표 코드가 현재 보유·획득 레벨을 제공하지 않으므로, 가져온 Lv6은 저장된 목표 Lv6이다. 현재 누적 레벨을 자동으로 추적하거나 목표 달성 여부를 판단하는 기능의 근거로 사용하지 않는다.

## 재생성

저장소 루트에서 실행한다. 아래 명령은 카탈로그 메타데이터만 갱신한다.

```powershell
python scripts/update_catalog.py --revision 34f57606015277cf9da76a2c7cc14fa1b68ccbbf --config-revision 225c0b76027f7345a712a2d62dbe7ee22de2f836
```

오프라인 재생성에는 다음 파일을 같은 자료 폴더에 보관한다. SSToy 파일은 위 고정 커밋의 `public/data/` 아래에서, `KR_Config.json`은 Hiro 고정 커밋의 `KR/bin/Config.json`에서 받은 원본 바이트다. 파일명에서 `/`만 `_`로 바꾸며, 내용·인코딩·개행을 변경하지 않는다.

| 오프라인 파일명 | 원본 SHA256 |
| --- | --- |
| `Character.json` | `3dfa44a86468346540740d08fef2c1a9339b1f3525ba8628654e61a24eb53488` |
| `Potential.json` | `85209f2e90d0516ea1858e0df6d76feab61645afde55d429c2354bc29cf236cb` |
| `CharPotential.json` | `366bbaa8a942f2850add71bbc14996633d8cc14e019f3a34486511cbd8c9e4d5` |
| `EN_Character.json` | `24a99347976f2cc0021a9162b4c3ace616c321373a6901e8b32479533f420ee5` |
| `KR_Character.json` | `ed617ce1899a0237dbaecd018d5db08c9d967e6f018f674713d8468701ea141d` |
| `Item.json` | `543b895e7a92437eb94701351fa93154dd08e0c4a1f8f87f0e966e7cf66e09ac` |
| `KR_Item.json` | `d20ffdddf8e86a18d91b66fbe42d6fb2039881755afa3d43c5ade519242c1f21` |
| `KR_Config.json` | `1b5366c8b5026ea24eab774bd0917718b0610cba26cd18df3f92555ad24c89eb` |

```powershell
python scripts/update_catalog.py --data-dir ..\asset-audit --revision 34f57606015277cf9da76a2c7cc14fa1b68ccbbf --config-revision 225c0b76027f7345a712a2d62dbe7ee22de2f836
python scripts/update_catalog.py --audit-only
```

생성된 카탈로그의 `source`에 실제 입력 바이트의 해시가 기록된다. `--data-dir`은 로컬 파일을 지정한 커밋에서 자동으로 인증하지 않으므로, 재현할 때 위 해시와 대조한다. `--audit-only`는 이미지 누락을 읽기 전용으로 보고한다. 캐릭터 이름 변경 시에도 기존 `key`를 보존하여 이미지 폴더 이름과의 연결을 유지한다.
