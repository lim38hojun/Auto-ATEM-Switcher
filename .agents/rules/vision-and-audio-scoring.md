# Vision & Audio Multi-Modal Scoring Rules

이 규칙은 `src/pipeline/vision_scorer.py` 및 `src/pipeline/audio_analyzer.py`를 수정하거나 새로운 카메라 평가 지표를 추가할 때 반드시 준수해야 하는 도메인 규칙입니다.

## 1. 점수 정규화 불변식 (Normalization Invariants)

모든 개별 카메라(Cam 1 ~ Cam 4)의 세부 지표는 반드시 닫힌 구간 `[0.0, 1.0]` 내의 부동소수점(`float`) 값으로 정규화되어야 합니다:

1. **구도 및 인물 안정성 점수 (`framing_score` $\in [0.0, 1.0]$)**:
   - 피사체(보컬, 연주자)의 중심점이 화면 중앙 골든 룰 영역(가로 $30\% \sim 70\%$, 세로 헤드룸 $15\% \sim 65\%$) 내에 안정적으로 위치할수록 `1.0`에 수렴합니다.
   - 피사체가 프레임 경계(상/하/좌/우 $5\%$ 이내)에 잘리거나(Edge Clipping) 화면 밖으로 이탈하면 즉시 페널티(`-0.4` 이상)를 부여합니다.
2. **모션 및 표정/연주 에너지 점수 (`motion_score` $\in [0.0, 1.0]$)**:
   - 피사체 영역 내 손가락 연주 동작, 상체 바운스, 시선/표정 변화를 프레임 차분 및 광류(Optical Flow) 크기로 산출합니다.
   - 단, 배경 전체가 흔들리는 글로벌 카메라 모션(Camera Pan/Shake)은 연주 에너지로 오인하지 않도록 중심 ROI 대비 외곽 배경 모션을 차감하여 계산합니다.
3. **선명도 및 안정성 점수 (`sharpness_score` $\in [0.0, 1.0]$)**:
   - 그레이스케일 프레임의 라플라시안 분산($\text{Var}(\nabla^2 I)$)을 계산하여 포커스 아웃 및 모션 블러를 검출합니다.
   - `laplacian_var < blur_threshold` (기본값 `45.0`)인 프레임은 `is_blurry = True`로 마킹하며, 최종 전환 후보에서 **즉시 제외(Disqualified)**합니다.
4. **오디오 솔로 가중치 (`audio_bonus` $\in [0.0, 1.0]$)**:
   - 특정 주파수 대역(예: 일렉트릭 기타 솔로 800Hz~3.5kHz 또는 보컬 대역)의 에너지가 급증할 때 해당 악기 전담 카메라(매핑된 카메라 ID)에 가중치 보너스를 부여합니다.

## 2. 통합 점수 산출식 (Composite Score Formula)

블러가 발생하지 않은 정상 카메라의 통합 점수 $S_i(t)$는 다음과 같이 가중 합산으로 계산합니다:

$$S_i(t) = \begin{cases} 
0.05 \cdot \text{sharpness\_score}_i(t), & \text{if } \text{is\_blurry}_i(t) = \text{True} \\
\text{clip}\Big(w_f F_i(t) + w_m M_i(t) + w_s \text{Sharp}_i(t) + w_a A_i(t),\ 0.0,\ 1.0\Big), & \text{otherwise}
\end{cases}$$

- 기본 가중치 합($w_f + w_m + w_s$)은 `1.0`을 기준으로 하되, 오디오 솔로 부스트($w_a A_i(t)$)가 활성화된 구간에서는 해당 전담 카메라가 최우선 순위로 선발될 수 있도록 허용합니다.
