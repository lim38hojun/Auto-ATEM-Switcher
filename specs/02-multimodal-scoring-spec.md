# Spec 02: Multi-Modal (Vision + Audio) Scoring Pipeline Specification

## 1. 시각 분석 파이프라인 (`VisionScorer`)

각 카메라 채널 프레임 $I_i(t)$ (분석 표준 해상도 `320x180`)에 대해 다음 3가지 시각 특징을 병렬 계산합니다.

### 1.1 피사체 구도 및 안정성 점수 ($F_i(t) \in [0.0, 1.0]$)
1. **피사체 검출 (Subject / Performer Localization)**:
   - 공연 무대 조명 및 인물 윤곽/얼굴/상체 활성 영역을 결합하여 주 피사체 바운딩 박스 $B_i = (x_c, y_c, w, h)$를 추출합니다.
2. **골든 룰 & 헤드룸 평가 (Golden Rule & Headroom)**:
   - 이상적인 인물 중심 좌표 $(x^*, y^*) = (0.50 \cdot W,\ 0.42 \cdot H)$와의 유클리드 거리에 따른 가우시안 감쇠 함수를 적용합니다:
     $$d_i = \sqrt{\left(\frac{x_c - 0.5W}{0.35W}\right)^2 + \left(\frac{y_c - 0.42H}{0.35H}\right)^2}, \quad F_{\text{center}, i} = \exp(-1.2 \cdot d_i^2)$$
3. **프레임 경계 잘림 감점 (Edge Clipping Penalty)**:
   - 피사체 박스가 화면 상단(헤드룸 부족) 또는 좌우 끝 $4\%$ 이내에 닿으면 `0.35`점을 감점합니다.

### 1.2 연주 및 표정 모션 에너지 ($M_i(t) \in [0.0, 1.0]$)
1. **피사체 국소 모션 vs. 배경 흔들림 분리**:
   - 피사체 내부 ROI의 프레임 간 변화량($E_{\text{fg}}$)과 배경 외곽 영역의 변화량($E_{\text{bg}}$)을 각각 측정합니다.
   - 순수 연주/표정 에너지 $E_{\text{net}} = \max(0, E_{\text{fg}} - 0.8 \cdot E_{\text{bg}})$를 계산하여, 카메라 자체가 흔들리는 경우에는 모션 점수가 오르지 않도록 설계합니다.

### 1.3 블러 및 카메라 흔들림 감지 ($\text{Sharpness}_i(t)$ & `is_blurry`)
1. **Laplacian Variance 계산**:
   $$L_i(t) = \text{Var}(\nabla^2 I_{\text{gray}, i}(t))$$
2. **제외 판정 (`is_blurry`)**:
   - $L_i(t) < \tau_{\text{blur}}$ (기본값 `45.0`)이거나 외곽 배경 전체의 급격한 흔들림($E_{\text{bg}} > \tau_{\text{shake}}$)이 감지되면 `is_blurry = True`로 설정하고 후보에서 즉시 탈락시킵니다.

---

## 2. 오디오 트리거 파이프라인 (`AudioAnalyzer`)

1. **드럼 킥/스네어 비트 피크 (`is_beat_peak`)**:
   - 저주파 대역(60~150Hz 킥 드럼) 및 중역 스네어 온셋 엔벨로프(Onset Envelope)에서 로컬 피크를 검출하여 음악의 박자(BPM)에 맞춘 전환 트리거 신호를 생성합니다.
2. **악기 솔로 대역 감지 (`active_solo_cam`)**:
   - 특정 악기 주파수 대역(예: 기타 솔로 `800Hz ~ 3.2kHz`, 보컬 하이라이트 `300Hz ~ 1.5kHz`)의 에너지 비율이 임계치를 넘으면 해당 악기 전담 카메라(기본 설정: `Cam 3 = Guitar`, `Cam 2 = Vocal`)에 `audio_bonus` (`+0.25`)를 부여합니다.
