---
name: multicam-testbench
description: >-
  Generates synchronized multi-camera concert/performance test datasets and evaluates camera selection tasks against ground-truth director timelines. Use this skill when testing the AI Director on multi-angle videos of the same scene, running automated benchmarks, or analyzing camera selection accuracy and blur avoidance rates.
---

# Multi-Camera Scene Selection Testbench Skill

이 스킬은 **"동일한 장면에 대한 여러 카메라 영상을 선택하는 과업(Multi-Camera Scene Selection Task)"**을 생성·실행하고 정량적으로 평가할 때 사용합니다.

## 1. 표준 과업 시나리오 (30초 음악 공연 4채널 시뮬레이션)

`python scripts/generate_synthetic_scene.py`를 실행하면 `data/synthetic/` 경로에 동일한 30초 공연 장면을 촬영한 4개의 동기화된 카메라 영상(`cam1_wide.mp4` ~ `cam4_roaming.mp4`)과 ATEM 10분할 멀티뷰 합성 영상(`multiview_10split.mp4`), 그리고 정답 연출 메타데이터(`scene_metadata.json`)가 생성됩니다.

| 구간 (초) | 장면 이벤트 (Scene Event) | Ground Truth 추천 카메라 | 핵심 검증 포인트 |
| :--- | :--- | :--- | :--- |
| `0.0s ~ 4.0s` | 오프닝 밴드 풀샷 | **Cam 1 (Wide)** 또는 **Cam 2 (Vocal)** | 초기 안정적 프레이밍 확보 및 최소 유지 시간 준수 |
| `4.0s ~ 10.0s` | 보컬 메인 파트 (정면 시선/클로즈업) | **Cam 2 (Vocal Close-up)** | 얼굴/중심 구도 점수 우위로 Cam 2 선택 |
| `10.0s ~ 18.0s` | 일렉트릭 기타 솔로 (고주파 오디오 피크 + 빠른 손 모션) | **Cam 3 (Guitar Solo)** | 오디오 솔로 가중치 + 모션 에너지 결합으로 Cam 3 자동 전환, 중간 `max_hold_sec`(7.5초) 도달 시 짧게 보조 앵글 순환 |
| `14.0s ~ 17.0s` | Cam 4 무빙캠 심한 흔들림 및 포커스 아웃 발생 | **Cam 4 절대 선택 금지** | Laplacian Variance 급감 감지 → `is_blurry=True` 후보 제외 검증 |
| `18.0s ~ 24.0s` | 하이라이트 합주 & 무빙 샷 (22~24초 Cam 4 재블러) | **Cam 4 (정상 구간)** → **Cam 2/1 (블러 회피)** | Cam 4가 22초에 블러 발생 시 즉시 비상 컷(`EMERGENCY_CUT`)으로 탈출하는지 검증 |
| `24.0s ~ 30.0s` | 엔딩 보컬 & 전체 무대 피날레 | **Cam 2 (Vocal)** → **Cam 1 (Wide)** | 비트 피크에 맞춘 엔딩 전환 |

## 2. 과업 실행 및 평가 방법

1. **CLI 자동 벤치마크 테스트**:
   ```powershell
   pytest tests/test_benchmark_task.py -v -s
   ```
2. **웹 대시보드 실시간 시뮬레이션 및 과업 평가**:
   - 서버 실행 후 브라우저(`http://127.0.0.1:8000`) 우측 하단의 **[과업 벤치마크 실행 (Run 30s Evaluation)]** 버튼을 클릭하면 전체 타임라인에 대한 **GT 일치율(%)**, **블러 회피율(100% 목표)**, **평균 샷 지속 시간(초)** 리포트가 즉시 출력됩니다.
