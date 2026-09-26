# Spec 00: ATEM AI Broadcast Director — System Architecture

## 1. 목적 및 배경

Blackmagic Design사의 **ATEM Mini Pro**는 4개의 HDMI 카메라 입력을 받아 자체 하드웨어 10분할 멀티뷰(Multiview)로 모니터링하고, 전용 소프트웨어(ATEM Software Control)나 이더넷/USB-C 프로토콜을 통해 화면을 전환(Switching)할 수 있는 라이브 프로덕션 스위처입니다.

본 시스템은 음악 공연이나 라이브 이벤트에서 **마치 숙련된 방송 PD나 스포츠 중계 자동화 시스템처럼 4대의 카메라 영상을 실시간 분석하여 가장 좋은 화면(Best Shot)을 선택하고 자동으로 ATEM 스위처에 전환 신호를 보내는 AI 방송 디렉터(AI Broadcast Director)**의 전체 아키텍처를 정의합니다.

---

## 2. 3대 핵심 서브시스템 구성

첨부 설계 자료의 아키텍처에 따라 시스템은 다음 세 가지 독립적이고 결합 가능한 레이어로 분리됩니다:

1. **다중 카메라 영상 수집 레이어 (`src/capture`)**:
   - **문제점 (ATEM Mini Pro 구조적 제약)**: ATEM Mini Pro의 USB-C 웹캠 출력은 4개 카메라 개별 스트림이 아닌, 현재 선택된 1개 화면(`Program`)만 PC로 전송합니다.
   - **해결 방안 A (멀티뷰 캡처 방식)**: ATEM의 HDMI OUT을 `Multiview(M/V)` 모드로 설정하고 HDMI-USB 캡처보드로 1080p 화면을 PC에 입력받은 뒤, OpenCV로 카메라 1~4 구역을 크롭(Crop)하여 4채널을 병렬 분석합니다.
   - **해결 방안 B (개별 스트림/파일/NDI 입력 방식)**: 4개의 개별 영상 파일 또는 네트워크/캡처 스트림을 동기화하여 분석하고, 스위칭 제어 명령만 ATEM으로 전송합니다.
2. **멀티모달(시각 + 오디오) 분석 파이프라인 (`src/pipeline`)**:
   - **Vision Pipeline**:
     - 인물/피사체 검출 및 구도 판별 (골든 룰, 헤드룸, 화면 중앙 안정성, 프레임 가장자리 이탈 여부)
     - 클로즈업 시선/얼굴 정면 응시 및 연주 동작(손가락 모션, 바운스) 속도/에너지 측정
     - 블러/흔들림 감지 (`Laplacian Variance` 계산으로 포커스 아웃 및 카메라 흔들림 프레임 후보 제외)
   - **Audio-driven Logic**:
     - 드럼 킥/스네어 비트 피크(BPM) 감지를 통한 컷 전환 타이밍 동기화
     - 악기 솔로 구간(특정 주파수 대역 및 레벨 상승) 감지 시 해당 악기 전담 카메라 가중치 상승
3. **방송 연출 룰 엔진 및 스위처 제어 레이어 (`src/engine` & `src/atem`)**:
   - **State Machine**: 최소 샷 유지 시간(2~3초), 최대 샷 유지 제한 시간(7~8초), 현재 카메라 제외 차순위 최고 점수 카메라 선정, 비트 스냅(Beat-Snap) 전환.
   - **ATEM Switcher Adapter**: `PyATEMMax` / `atem-connection` 호환 UDP 제어기 및 하드웨어 없이 동작하는 `MockATEMSwitcher` 내장.

---

## 3. 프레임 단위 데이터 흐름 (Data Schema)

```python
@dataclass
class ChannelMetrics:
    cam_id: int                  # 1, 2, 3, 4
    framing_score: float         # [0.0, 1.0] 골든 룰 / 중심 구도 점수
    motion_score: float          # [0.0, 1.0] 연주/표정 모션 에너지
    sharpness_score: float       # [0.0, 1.0] 정규화된 라플라시안 선명도
    laplacian_var: float         # Raw Laplacian Variance 값
    audio_bonus: float           # [0.0, 1.0] 솔로 악기 매칭 가중치 보너스
    is_blurry: bool              # True일 경우 후보에서 즉시 제외
    composite_score: float       # 최종 합산 점수 S_i(t)
    subject_bbox: Tuple[int, int, int, int]  # (x, y, w, h) 검출된 피사체 영역
```
