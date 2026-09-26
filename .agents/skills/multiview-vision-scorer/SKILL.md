---
name: multiview-vision-scorer
description: >-
  Calibrates ATEM Mini Pro 10-split Multiview ROI crop regions and tunes Computer Vision scoring parameters (framing golden rule, optical flow motion energy, and Laplacian variance blur exclusion). Use this skill when modifying src/capture/ingestor.py or src/pipeline/vision_scorer.py, or when camera crop coordinates or vision scores need calibration.
---

# Multiview Vision Scorer & ROI Calibration Skill

이 스킬은 ATEM Mini Pro의 HDMI OUT **10분할 멀티뷰(Multiview)** 영상을 4개의 카메라 채널(Cam 1~4)로 정밀 크롭(Crop)하거나, 각 카메라의 시각 분석 가중치(`Framing`, `Motion`, `Sharpness`)를 튜닝할 때 사용합니다.

## 1. ATEM 10분할 멀티뷰 크롭 절차

ATEM Mini Pro의 USB-C 출력은 단일 `Program` 화면만 전송하므로, HDMI OUT을 `M/V`(Multiview) 모드로 설정하고 HDMI-USB 캡처보드로 1080p(`1920x1080`) 프레임을 수집합니다.

1. 정확한 1080p 픽셀 좌표계 규격은 [multiview_layout_1080p.md](./references/multiview_layout_1080p.md)를 참조하십시오.
2. 크롭 시 하단 오디오 미터 바(Audio Meter Overlay)와 카메라 라벨(`Cam 1`~`4`) 텍스트 박스가 모션/블러 연산을 오염시키지 않도록 **안전 마진(Safe Margin, 상하좌우 4% 안쪽)**을 적용한 내부 ROI를 추출합니다.

## 2. 시각 점수(Vision Score) 튜닝 가이드

`src/pipeline/vision_scorer.py`의 `VisionConfig` 파라미터를 조정할 때 다음 기준을 따릅니다:

- **`blur_threshold` (기본값 `45.0`)**:
  - `cv2.Laplacian(gray, cv2.CV_64F).var()` 값이 이 임계값 미만이면 `is_blurry = True`로 판정하여 전환 대상에서 즉시 제외합니다.
  - 조명이 어두운 공연장에서는 노이즈 억제 필터로 인해 라플라시안 분산이 낮아질 수 있으므로 `30.0 ~ 40.0`으로 하향 조정합니다.
- **`w_framing` / `w_motion` / `w_sharpness`**:
  - 발라드/어쿠스틱 공연: 구도와 인물 중심 안정성 중시 (`w_framing=0.55, w_motion=0.20, w_sharpness=0.25`)
  - 록/밴드/댄스 공연: 연주 에너지와 역동성 중시 (`w_framing=0.35, w_motion=0.45, w_sharpness=0.20`)

## 3. 검증 단계 (Validation)

변경 후 반드시 다음 단위 테스트를 실행하여 블러 프레임 제외 및 멀티뷰 크롭 정확도를 확인합니다:

```powershell
pytest tests/test_vision_scorer.py tests/test_multiview_crop.py -v
```
