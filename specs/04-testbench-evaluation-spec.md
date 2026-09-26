# Spec 04: Multi-Camera Scene Selection Task & Evaluation Benchmark Specification

## 1. 과업 정의 (Scene Camera Selection Task)

**"동일한 장면에 대한 여러 카메라 영상을 선택하는 과업"**은 시간 축 $t \in [0, T]$ 동안 동기화된 4개의 카메라 영상 $\{I_1(t), I_2(t), I_3(t), I_4(t)\}$과 오디오 신호 $A(t)$를 입력받아 매 시점 송출할 최적의 Program 카메라 $y_{\text{pred}}(t) \in \{1, 2, 3, 4\}$를 결정하는 순차적 의사결정 과업입니다.

---

## 2. 정량적 평가지표 (Evaluation Metrics)

테스트벤치(`POST /api/benchmark/run` 및 `tests/test_benchmark_task.py`)는 다음 4가지 정량 지표를 자동으로 계산하여 리포트합니다:

1. **추천 연출 일치율 (Ground-Truth Alignment Accuracy, %)**:
   - 각 구간별로 정의된 허용 정답 카메라 집합 $Y_{\text{GT}}(t)$ (예: 보컬 구간 `{2}`, 기타 솔로 구간 `{3, 1}`) 안에 AI 디렉터의 선택 $y_{\text{pred}}(t)$가 포함되는 프레임 비율입니다.
   - 단, 전환 과도기(`±0.8초` 비트 동기화 여유 구간)는 정상 전환으로 인정합니다.
2. **블러/흔들림 회피율 (Blur Avoidance Rate, %)**:
   - 전체 테스트 시간 중 `Program`으로 송출된 카메라가 블러/흔들림 상태(`is_blurry == True`)가 **아닌** 프레임의 비율입니다.
   - 목표 기준: **98.0% 이상** (가상 시나리오의 Cam 4 흔들림 구간을 완벽히 회피하거나 즉시 비상 탈출해야 함).
3. **샷 안정성 및 평균 유지 시간 (Mean Shot Duration & Flicker Violation Count)**:
   - 전체 컷 전환 사이의 평균 지속 시간($\bar{D}_{\text{shot}}$)이 `[2.3초, 8.0초]` 범위 내에 있는지 확인합니다.
   - 비상 블러 회피(`EMERGENCY_CUT`)를 제외한 일반 전환 중 `min_hold_sec` 미만에 발생한 깜빡임 위반(Flicker Violation) 횟수가 **0회**여야 합니다.
4. **비트 동기화 정확도 (Beat-Sync Alignment Ratio, %)**:
   - 전체 일반 컷 전환 시점 중 드럼 비트 피크(`±150ms` 이내)와 동기화되어 전환된 비율을 측정합니다.
