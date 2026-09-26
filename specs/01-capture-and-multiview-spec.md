# Spec 01: Multi-Camera Capture & ATEM Multiview Cropping Specification

## 1. 개요

본 명세서는 `src/capture/ingestor.py`에서 지원하는 4가지 다중 카메라 입력 모드와 ATEM Mini Pro의 10분할 멀티뷰 HDMI 출력을 4개의 독립 분석 채널로 변환하는 규격을 정의합니다.

---

## 2. 4대 입력 모드 (Ingestion Modes)

| 모드 식별자 | 설명 | 주요 활용 시나리오 |
| :--- | :--- | :--- |
| `synthetic` | 내장 가상 공연 생성기(`data/synthetic/cam1~4.mp4`) 로드 | 외부 카메라나 영상 없이 즉시 동일 장면 카메라 선택 과업 테스트 |
| `quad_files` | 사용자가 지정한 4개의 개별 동기화 영상 파일(`Cam 1~4`) 동시 디코딩 | 다중 카메라로 녹화된 실제 공연/행사 영상을 사후(Offline/Real-time) 분석 |
| `multiview_crop` | 단일 1080p ATEM 10분할 멀티뷰 영상/스트림에서 4개 ROI 크롭 | ATEM Mini Pro HDMI OUT → HDMI-USB 캡처보드 연결 환경 또는 멀티뷰 녹화본 테스트 |
| `live_webcam` | 시스템에 연결된 물리 캡처보드/웹캠 인덱스(`cv2.VideoCapture`) 연결 | 실제 현장 라이브 스트리밍 및 실시간 스위칭 테스트 |

---

## 3. ATEM Mini Pro 10분할 멀티뷰 크롭 규격

ATEM Mini Pro의 기본 멀티뷰 출력(`1920x1080`)에서 중단 행(Middle Row, `y = 540 ~ 810`)에 4개의 카메라 입력(`Cam 1` ~ `Cam 4`)이 각 `480x270` 크기로 배치됩니다.

### 3.1 오버레이 오염 방지 (Overlay Exclusion)
각 카메라 셀에는 다음 하드웨어 UI 오버레이가 포함됩니다:
- 외곽 `3px` 두께의 Tally 테두리 (Program=빨강 `#FF0000`, Preview=초록 `#00FF00`)
- 하단 `28px` 높이의 카메라 라벨 바 (`Cam 1` 텍스트 및 좌측 오디오 레벨 미터)

이 UI 요소들이 광류(Optical Flow)나 라플라시안 블러 연산에 영향을 주지 않도록 다음과 같이 정규화된 상대 좌표(`NormalizedROI`)로 크롭합니다 ($W=1920, H=1080$ 기준):

- **Cam 1 Safe ROI**: `x1=0.004, y1=0.505, x2=0.246, y2=0.724` → 픽셀 `[546:782, 8:472]`
- **Cam 2 Safe ROI**: `x1=0.254, y1=0.505, x2=0.496, y2=0.724` → 픽셀 `[546:782, 488:952]`
- **Cam 3 Safe ROI**: `x1=0.504, y1=0.505, x2=0.746, y2=0.724` → 픽셀 `[546:782, 968:1432]`
- **Cam 4 Safe ROI**: `x1=0.754, y1=0.505, x2=0.996, y2=0.724` → 픽셀 `[546:782, 1448:1912]`

해상도가 `1280x720` 등 다른 크기로 입력되더라도 위 정규화 비율을 곱하여 자동으로 정확한 4채널 영역을 추출합니다.
