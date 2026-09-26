# AGENTS.md — ATEM Mini Pro AI Broadcast Director & Multi-Camera Selection Testbench

이 저장소는 **Blackmagic Design ATEM Mini Pro**의 하드웨어 멀티뷰(Multiview) 및 오픈소스 제어 프로토콜(`PyATEMMax` / `atem-connection`)을 활용하여, **OBS Studio로 녹화 및 스트리밍을 진행하는 동시에** 스위처에 연결된 **2대, 3대 또는 4대의 카메라(Cam 1~4)** 입력 중 **가장 좋은 화면(Best Shot)**을 실시간(< 4ms)으로 판별하고 자동 스위칭하는 **네이티브 로컬 AI 방송 디렉터(Local AI Broadcast Director)** 시스템입니다.

---

## 1. 프로젝트 디렉토리 구조 (Project Map)

```text
.
├── AGENTS.md                                 # 루트 에이전트 거버넌스 및 아키텍처 가이드라인
├── run_local_director.py                     # 네이티브 로컬 데스크톱 프로그램(PyQt5) 즉시 실행 런처
├── .agents/
│   ├── rules/                                # 도메인별 세부 불변 규칙 (자동 로드)
│   │   ├── vision-and-audio-scoring.md       # 시각/오디오 스코어링 정규화, 블러 및 무신호 포트 제외 규칙
│   │   └── broadcast-state-machine.md        # 2/3/4채널 가변 카메라 연출 휴리스틱 및 깜빡임 방지 불변식
│   └── skills/                               # 온디맨드 전문 에이전트 스킬 런북
│       ├── multiview-vision-scorer/          # ATEM 10분할 멀티뷰 크롭 및 CV 가중치 튜닝 스킬
│       ├── direction-rule-engine/            # 연출 State Machine 및 BPM 비트 동기화 스킬
│       ├── atem-switcher-control/            # ATEM Mini Pro 하드웨어/Mock 스위처 제어 스킬
│       └── multicam-testbench/               # 동일 장면 2/3/4채널 카메라 선택 과업 벤치마크 스킬
├── specs/                                    # 시스템 설계 및 알고리즘 기술 명세서 (00~04)
│   ├── 00-system-architecture.md
│   ├── 01-capture-and-multiview-spec.md
│   ├── 02-multimodal-scoring-spec.md
│   ├── 03-broadcast-state-machine-spec.md
│   └── 04-testbench-evaluation-spec.md
├── scripts/
│   └── generate_synthetic_scene.py           # 4채널 가상 공연 장면 및 ATEM 10분할 멀티뷰 영상 생성기
├── src/
│   ├── desktop_app.py                        # [핵심] OBS 병행 구동용 PyQt5 고속 네이티브 로컬 데스크톱 앱
│   ├── capture/ingestor.py                   # 2/3/4채널 가변 인제스터 (DirectShow 캡처보드/파일/멀티뷰크롭)
│   ├── pipeline/vision_scorer.py             # 무신호(No Signal) 자동 감지, 구도, 모션, 라플라시안 블러 분석기
│   ├── pipeline/audio_analyzer.py            # 드럼 킥/스네어 BPM 피크 및 솔로 주파수 감지
│   ├── engine/state_machine.py               # 2~4채널 가변 최소/최대 샷 유지 및 무신호/블러 비상 탈출 엔진
│   ├── atem/controller.py                    # MockATEMSwitcher 및 PyATEMMax 실기기 UDP 브릿지
│   └── server/app.py                         # 선택적 FastAPI 테스트벤치 및 2/3/4채널 과업 벤치마크 평가기
└── tests/                                    # 단위 테스트 및 2/3/4채널 과업 평가 통합 테스트
```

---

## 2. 핵심 아키텍처 불변 규칙 (Core Architectural Invariants)

1. **OBS Studio 라이브 녹화/스트리밍과의 완벽한 병행 (OBS + Local AI Director Coexistence)**:
   - **OBS Studio**는 ATEM Mini Pro의 **USB-C 웹캠 출력(최종 전환된 Program 비디오)**을 독점 입력받아 자막·오버레이 합성 및 녹화/스트리밍을 수행합니다.
   - **로컬 AI 디렉터(`run_local_director.py`)**는 웹 브라우저 오버헤드 없이 네이티브 C++/Qt 메모리 루프(30fps, 프레임당 연산 `< 4ms`)로 동작하며, **ATEM의 HDMI OUT(멀티뷰 모드)에 연결된 보조 HDMI-USB 캡처보드(`cv2.CAP_DSHOW`)**에서 카메라 영역들을 실시간 크롭하여 즉시 판단을 내리고 이더넷(UDP `PyATEMMax`)으로 ATEM 본체에 컷 전환 신호를 전송합니다.
2. **가변 카메라 대수 완벽 대응 ($N \in \{2, 3, 4\}$ Cameras & Auto No-Signal Exclusion)**:
   - 항상 4대의 카메라가 연결되어 있지 않아도 됩니다. **2대(`Cam 1, 2`)**나 **3대(`Cam 1, 2, 3`)**만 연결된 상태에서도 100% 정상 작동해야 합니다.
   - `VisionScorer`는 연결되지 않은 빈 HDMI 포트(블랙 화면 / `has_signal == False`)를 자동으로 감지하여 스위칭 후보에서 즉시 제외하며, 송출 중이던 카메라 케이블이 빠질 경우 `BroadcastStateMachine`이 1프레임 내에 `SIGNAL_LOSS_CUT`으로 연결된 다른 정상 카메라로 즉시 복구합니다.
3. **연출 룰 엔진 강제 경유 (Mandatory State Machine Gatekeeping)**:
   - 모든 스위칭 명령은 `BroadcastStateMachine`을 통과하여 **최소 샷 유지 시간(2.0~3.0초)** 및 **최대 샷 제한 시간(7.0~8.0초)** 검증을 거쳐야 합니다.

---

## 3. 자주 사용하는 명령어 (Quick Commands)

- **네이티브 로컬 데스크톱 프로그램 실행 (OBS 병행 라이브 스위칭 & 2/3/4채널 테스트벤치)**:
  ```powershell
  python run_local_director.py
  ```
- **2대 / 3대 / 4대 카메라 선택 과업 통합 테스트 검증**:
  ```powershell
  pytest tests/ -v
  ```
