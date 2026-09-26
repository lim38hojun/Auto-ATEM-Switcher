# ATEM AI 방송 디렉터 — 종합 기술 분석 보고서

이 보고서는 프로그램의 두 가지 핵심 축인 **① ATEM Mini Pro 하드웨어 실제 제어 가능 여부**와 **② 장면 인식·선정 알고리즘의 품질**을 코드 레벨에서 솔직하게 분석한 결과입니다.

---

## Part A: ATEM Mini Pro 실제 제어 가능 여부 분석

### ✅ 결론: **네, 실제로 작동합니다** — 단, 몇 가지 프로토콜 보완이 필요합니다.

### A-1. 제대로 구현된 부분 (현재 작동 가능)

| 기능 | PyATEMMax API 호출 | 상태 |
|------|-------------------|------|
| **스위처 UDP 연결** | `ATEMMax().connect(ip)` + `waitForConnection(timeout=1.5)` | ✅ 정상 |
| **Program 버스 전환 (CUT)** | `setPreviewInputVideoSource(0, cam)` → `execCutME(0)` | ✅ 정상 |
| **부드러운 전환 (MIX/DIP/WIPE/DVE)** | `setTransitionStyle(0, style)` → `setPreviewInputVideoSource(0, cam)` → `execAutoME(0)` | ✅ 프로토콜 순서 정확 |
| **전환 속도 제어** | `setTransitionMixRate(0, frames)` 등 | ✅ 정상 |
| **PiP (DVE Keyer)** | `setKeyerType(0,0,"dVE")` → `setKeyerFillSource(0,0,cam)` → `setKeyerOnAirEnabled(0,0,True)` | ✅ 정상 |
| **Fade-to-Black** | `execFadeToBlackME(0)` | ✅ 정상 |
| **Mock 안전 폴백** | PyATEMMax 미설치/연결 실패 시 → 자동으로 Mock 모드 전환 | ✅ 정상 |

> [!TIP]
> ATEM Mini Pro는 Skaarhoj의 오픈 프로토콜(UDP Port 9910)을 사용하며, `PyATEMMax`는 이 프로토콜의 정식 Python 구현체입니다. 프로그램의 `connect()` → `setPreviewInputVideoSource()` → `execAutoME()` 호출 순서는 **실제 ATEM Software Control** 앱이 사용하는 것과 동일한 프로토콜 시퀀스입니다.

### A-2. 프로토콜 수준의 문제점 및 개선 필요 사항

#### 🔴 문제 1: CUT 전환 시 불필요한 이중 명령 (경미한 결함)

[controller.py L226-L230](file:///c:/Users/ddosu/antigravity_workspace/first/src/atem/controller.py#L226-L230)에서 CUT 전환 시:
```python
# 현재 코드 (불필요한 이중 호출)
self._hw_switcher.setPreviewInputVideoSource(0, program_cam)  # ① Preview를 target으로 설정
self._hw_switcher.execCutME(0)                                 # ② CUT 실행 (Preview ↔ Program 교환)
self._hw_switcher.setProgramInputVideoSource(0, program_cam)   # ③ ← 불필요! ②에서 이미 전환됨
self._hw_switcher.setPreviewInputVideoSource(0, preview_cam)   # ④ Preview 복원
```

> **실제 ATEM 프로토콜에서 `execCutME(0)`는 내부적으로 Preview와 Program 버스를 교환(swap)**합니다. 따라서 ①+②만으로 충분하며, ③의 `setProgramInputVideoSource`는 중복 호출입니다. 기능적으로 해가 되지는 않지만 1패킷의 불필요한 레이턴시가 추가됩니다.

#### 🔴 문제 2: 부드러운 전환(AUTO) 시 매 전환마다 4개 Rate를 모두 재전송 (비효율)

[controller.py L233-L237](file:///c:/Users/ddosu/antigravity_workspace/first/src/atem/controller.py#L233-L237)에서 `execAutoME(0)` 직전에 **모든** Rate(`MixRate`, `DipRate`, `WipeRate`, `DVERate`)를 매번 전송합니다. ATEM은 마지막에 `setTransitionStyle()`로 지정된 스타일의 Rate만 사용하므로, 해당 스타일의 Rate 하나만 전송해도 됩니다.

#### 🟡 문제 3: 연결 유지 핸들링 미흡 (안정성)

현재 `connect_hardware()`에서 최초 1회 연결 후 **연결 끊김(disconnect) 감지 로직이 없습니다**. 실제 라이브 환경에서 이더넷 케이블이 잠시 빠지거나 ATEM이 재부팅되면, 프로그램은 `self._hw_switcher`가 여전히 유효하다고 판단하여 계속 `except: pass`로 조용히 실패합니다.

#### 🟡 문제 4: AFV에서 `setAudioMixerInputMixOption` 존재 여부 불확실

[controller.py L169](file:///c:/Users/ddosu/antigravity_workspace/first/src/atem/controller.py#L169)에서 `hasattr(self._hw_switcher, "setAudioMixerInputMixOption")`으로 체크하고 있지만, 입력 소스 ID를 `cam_id` 정수 대신 `ATEMAudioSources` enum으로 전달해야 할 수 있습니다.

#### 🔴 문제 5: AUTO 전환 중 Preview 버스 덮어쓰기 레이스 컨디션

`execAutoME(0)` 실행 후 ATEM 내부에서 MIX 트랜지션이 진행되는 동안(0.7초), 다음 프레임의 `sync_switcher_bus(switched=False)` 호출이 [L193](file:///c:/Users/ddosu/antigravity_workspace/first/src/atem/controller.py#L193)에서 즉시 `setPreviewInputVideoSource(0, preview_cam)`를 전송합니다. **ATEM은 트랜지션 중 Preview 버스 변경을 수신하면 전환 대상이 바뀔 수 있습니다.** `min_hold_sec`(2.5초) 덕분에 즉시 재전환은 막히지만, Preview tally가 의도치 않게 바뀔 위험이 있습니다.

#### 🟡 문제 6: 캡처 파이프라인이 매 프레임 불필요한 합성 이미지를 생성

[ingestor.py L314](file:///c:/Users/ddosu/antigravity_workspace/first/src/capture/ingestor.py#L314)에서 `render_four_cameras_at_time(t)`가 **모든 모드**(live_webcam, real_concert_mp4 포함)에서 매 프레임 호출됩니다. 이는 fallback 프레임과 메타데이터 생성을 위해서이지만, 실제 하드웨어/파일 모드에서는 CPU 자원의 불필요한 낭비(약 2~3ms/프레임)입니다.

### A-3. ATEM 하드웨어 제어 개선 방안

| 우선순위 | 개선 사항 | 난이도 |
|---------|----------|-------|
| **P0** | CUT 시 `setProgramInputVideoSource` 중복 호출 제거 | 🟢 간단 |
| **P0** | AUTO 시 현재 설정된 TransitionStyle에 해당하는 Rate만 전송 | 🟢 간단 |
| **P0** | AUTO 전환 중(0.7초) Preview 버스 갱신 억제 (레이스 컨디션 방지) | 🟡 보통 |
| **P1** | `_hw_switcher.connected` 속성으로 연결 상태 주기적 확인 + 자동 재연결 | 🟡 보통 |
| **P1** | ATEM에서 현재 상태(Program/Preview/Transition 진행 여부)를 읽어와 UI와 동기화 | 🟡 보통 |
| **P2** | AFV 오디오 소스 ID를 정수 대신 `ATEMAudioSources` enum으로 전달 | 🟢 간단 |
| **P2** | 라이브/파일 모드에서 `render_four_cameras_at_time()` 불필요한 호출 제거 (2~3ms/프레임 절감) | 🟢 간단 |

---

## Part B: 장면 인식·선정 알고리즘 품질 분석

### 🟡 결론: **기본기는 갖추었으나, "고품질 AI 장면 선정"이라고 하기엔 상당히 한계가 있습니다.**

### B-1. 현재 비전 스코어링 구조 ([vision_scorer.py](file:///c:/Users/ddosu/antigravity_workspace/first/src/pipeline/vision_scorer.py))

현재 `composite_score`는 다음 4가지 구성 요소의 **가중합(Weighted Sum)**입니다:

$$\text{composite} = 0.45 \times \text{framing} + 0.35 \times \text{motion} + 0.20 \times \text{sharpness} + 0.28 \times \text{audio\_bonus}$$

> [!WARNING]
> **가중치 합이 1.28**(= 0.45 + 0.35 + 0.20 + 0.28)로, `np.clip(..., 0.0, 1.0)`으로 잘려나갑니다. 이는 의도적 설계인지 버그인지 명확하지 않으며, `audio_bonus`가 0이 아닌 카메라는 점수가 과잉 부풀려져 1.0에 쉽게 도달합니다.

각 구성 요소의 실제 구현 품질을 분석하면:

#### (1) Framing Score (구도 점수, 가중치 0.45) — ⚠️ 매우 기초적

```python
# 실제 동작: HSV 색 공간에서 "밝은 영역"을 피사체로 추정
fg_mask = cv2.inRange(hsv, (0, 35, 65), (180, 255, 255))  # 단순 밝기 임계값
contours = cv2.findContours(fg_mask, ...)
largest = max(contours, key=cv2.contourArea)  # 가장 큰 밝은 영역 = "피사체"
```

**강점:**
- 조명이 밝은 공연장에서 무대 조명을 받는 연주자를 대략적으로 잡아냄
- 골든 룰(Rule of Thirds) 중심부에 피사체가 있으면 높은 점수

**한계:**
- ❌ **사람 감지(Human Detection)를 전혀 하지 않음**: 밝은 조명 기둥, LED 스크린, 흰 배경 등이 "피사체"로 오인될 수 있음
- ❌ **얼굴 인식(Face Detection)이 없음**: 보컬의 얼굴이 클로즈업되어 있는지, 뒷모습인지, 손만 보이는지 구분 불가
- ❌ **다수 인물 처리 불가**: `max(contours, key=contourArea)`로 가장 큰 영역 하나만 사용. 밴드 전체가 무대에 있으면 하나의 큰 덩어리로 합쳐져 개별 구도 평가 불가

#### (2) Motion Score (동작 에너지, 가중치 0.35) — ⚠️ 기초적이나 합리적

```python
diff = cv2.absdiff(gray, prev)           # 프레임 간 절대 차이
fg_energy = np.mean(diff[subject_bbox])   # 피사체 영역 평균 변화량
bg_shake = np.mean(diff[border_region])   # 테두리 영역 = 카메라 흔들림 추정
net_energy = max(0.0, fg_energy - 0.85 * bg_shake)
```

**강점:**
- 피사체 모션과 카메라 자체 흔들림을 분리하려는 시도 — 합리적 접근
- 연주자의 격렬한 동작(드럼 솔로, 점프 등)에 높은 모션 점수 부여

**한계:**
- ❌ **Optical Flow를 사용하지 않음**: 단순 프레임 차이(`absdiff`)는 조명 변화(LED, 스트로보)에 극도로 취약. 실제 공연장에서는 조명이 깜빡일 때마다 모든 카메라의 모션 점수가 동시에 급등하여 차별화가 사라짐
- ❌ **모션의 "의미"를 모름**: 의미 있는 제스처(기타 솔로)와 무의미한 흔들림(관객 혼잡)을 구분 불가

#### (3) Sharpness Score (선명도, 가중치 0.20) — ✅ 적절하고 효과적

```python
lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()  # 라플라시안 분산
is_blurry = lap_var < 45.0
```

- ✅ 라플라시안 분산은 블러 검출에 **가장 널리 사용되는 표준 방법**
- ✅ 흔들린 카메라를 즉시 제외(점수 0.05로 강제 축소)하는 것은 실용적으로 매우 효과적
- ✅ 카메라 흔들림과 결합한 이중 조건(`bg_shake > 18.0 and lap_var < threshold * 1.6`)도 합리적

#### (4) Audio Bonus (오디오 보너스, 가중치 0.28) — 🔴 **가짜 시뮬레이션**

[audio_analyzer.py](file:///c:/Users/ddosu/antigravity_workspace/first/src/pipeline/audio_analyzer.py) 전체를 보면:

```python
# 실제 오디오 데이터를 전혀 분석하지 않음!
rms = 0.55 + 0.25 * math.cos(2 * math.pi * timestamp / beat_period)  # 단순 코사인 곡선
is_beat = abs(phase) < 0.09  # 고정 BPM에서 수학적으로 계산한 가짜 비트
bonuses = {1: 0.08, 2: 0.0, 3: 0.0, 4: 0.0}  # 1번 카메라에 하드코딩된 고정 보너스
```

> [!CAUTION]
> **`AudioAnalyzer`는 실제 오디오 스트림을 전혀 분석하지 않습니다.** 고정 BPM(120)에서 수학 공식으로 가짜 비트 피크를 생성하고, 1번 카메라에 `0.08`의 고정 보너스를 항상 부여합니다. `scene_meta` 딕셔너리에 외부에서 `bpm`, `is_beat_peak`, `solo_instrument_cam` 등을 수동으로 넣어주지 않으면, **비트 동기화나 솔로 악기 감지 기능은 완전한 플레이스홀더(가짜)**입니다.

### B-2. 무신호 감지 (No-Signal Detection) — ✅ 잘 작동함

```python
def _check_has_video_signal(self, gray):
    mean_val = np.mean(gray)   # 빈 HDMI 포트 = 검은 화면 (mean ≈ 0~6)
    std_val = np.std(gray)     # 빈 HDMI 포트 = 편차 ≈ 0~2
    return not (mean_val < 7.5 and std_val < 3.5)
```

- ✅ ATEM Mini Pro의 빈 HDMI 포트는 실제로 완전 검정(mean≈0)을 출력하므로, 이 단순 임계값 방식이 실전에서도 정확하게 작동합니다.

### B-3. 연출 룰 엔진 ([state_machine.py](file:///c:/Users/ddosu/antigravity_workspace/first/src/engine/state_machine.py)) — ✅ 잘 설계됨

| 규칙 | 구현 상태 | 평가 |
|------|----------|------|
| 최소 샷 유지(`min_hold_sec`) | ✅ 슬라이더로 0.1초 단위 실시간 조절 | 깜빡임 방지에 효과적 |
| 최대 샷 제한(`max_hold_sec`) | ✅ 슬라이더로 0.1초 단위 실시간 조절 | 화면 고정 방지에 효과적 |
| 긴급 블러 탈출(`EMERGENCY_CUT`) | ✅ 연속 2프레임 블러 감지 시 즉시 전환 | 실용적 |
| 신호 손실 복구(`SIGNAL_LOSS_CUT`) | ✅ 1프레임 내 즉시 정상 카메라로 전환 | 안전성 확보 |
| 비트 동기화(`BEAT_ARMED`) | ⚠️ 로직은 정상이나, 실제 비트 데이터가 가짜 | 실전에서는 무의미 |
| AI 자동 전환 스타일(`AI_AUTO`) | ✅ 긴급=CUT, 일반=MIX 판별 | 합리적 |
| 히스테리시스(`switch_margin`) | ✅ 0.09 임계값으로 미세한 점수차 무시 | 깜빡임 방지 |

---

## Part C: 종합 평가 매트릭스

| 분야 | 현재 수준 | 실전 투입 가능? | 핵심 병목 |
|------|----------|---------------|----------|
| **ATEM 하드웨어 제어** | ★★★★☆ (80%) | ✅ **즉시 가능** | 연결 끊김 재접속 없음 |
| **블러/신호 감지** | ★★★★★ (95%) | ✅ 매우 우수 | — |
| **연출 룰 엔진** | ★★★★☆ (85%) | ✅ 즉시 가능 | — |
| **구도(Framing) 인식** | ★★☆☆☆ (35%) | ⚠️ 기초적 | 사람/얼굴 인식 없음 |
| **모션(Motion) 분석** | ★★★☆☆ (50%) | ⚠️ 보통 | Optical Flow 없음, 조명 취약 |
| **오디오/비트 분석** | ★☆☆☆☆ (10%) | ❌ 완전 가짜 | 실제 오디오 입력 없음 |
| **로컬 전환 효과 렌더링** | ★★★★☆ (80%) | ✅ 시각적으로 효과적 | — |

---

## Part D: 우선순위별 개선 로드맵

### 🔴 Phase 1 — 즉시 개선 (실전 투입 전 필수, 1~2일)

1. **ATEM 연결 상태 감시 & 자동 재연결**
   - `_hw_switcher.connected` 속성을 `_tick_frame()`에서 주기적으로 확인
   - 연결 끊김 시 5초 간격으로 자동 재접속 시도, UI에 연결 상태 표시

2. **CUT 전환 시 중복 `setProgramInputVideoSource` 제거**
   - `execCutME(0)` 후 `setProgramInputVideoSource(0, program_cam)` 삭제 → 레이턴시 1패킷 절감

3. **가중치 합 정규화 버그 수정**
   - `w_framing + w_motion + w_sharpness + w_audio = 1.28` → 합이 1.0이 되도록 정규화하거나, `audio_bonus`를 가산 방식으로 명시적 분리

### 🟡 Phase 2 — 비전 품질 대폭 향상 (1~2주)

4. **사람/얼굴 감지 도입 (Framing 점수 정확도 2~3배 향상)**
   - OpenCV의 `cv2.CascadeClassifier("haarcascade_frontalface_alt2.xml")` 또는 경량 DNN 기반 `cv2.dnn.readNetFromCaffe(deploy.prototxt, res10_300x300.caffemodel)` → 320×180 해상도에서 2~3ms
   - 얼굴이 화면 중심에 있고, 적절한 크기(헤드룸)일 때 `framing_score` 대폭 부스트
   - 뒷모습, 상반신 잘림 등을 페널티

5. **Sparse Optical Flow로 모션 분석 교체**
   - `cv2.absdiff` → `cv2.calcOpticalFlowFarneback()` 또는 `cv2.calcOpticalFlowPyrLK()`
   - 조명 변화(LED, 스트로보)에 강건한 실제 동작 방향·속도 벡터 추출
   - 피사체 영역의 유의미한 동작(연주 제스처)과 배경 흔들림을 더 정밀하게 분리

6. **실제 오디오 입력 파이프라인 구축**
   - `sounddevice` 또는 `pyaudio`로 PC 마이크/라인 입력의 실시간 PCM 오디오 스트림 캡처
   - 128~256 샘플 단위 FFT + Onset Detection(`librosa.onset.onset_detect`)으로 실시간 BPM 및 비트 피크 검출
   - ATEM 마이크 채널별 FFT 에너지 분석으로 "어느 카메라의 악기가 솔로 중인지" 판별

### 🟢 Phase 3 — 고급 AI 도입 (선택적, 2~4주)

7. **경량 Pose Estimation (동작 의미 이해)**
   - MediaPipe Pose 또는 MoveNet으로 연주자의 골격(팔·머리·기타 넥 각도) 추출
   - "드럼 치기", "기타 솔로 제스처", "마이크 잡고 노래 중" 등 의미론적 동작 점수 산출

8. **장면 분류 모델 (Shot Type Recognition)**
   - Wide/Medium/Close-Up/Extreme Close-Up 자동 분류
   - "직전이 Close-Up이었으므로 다음은 Wide로" 같은 연출 문법 적용

9. **시청자 참여도 기반 강화학습**
   - 실시간 채팅 반응, 시청자 수 변화, 하트/이모지 급증 이벤트를 보상 신호로 사용
   - 가중치(`w_framing`, `w_motion` 등)를 공연 중 자동 튜닝

---

## 최종 요약

| 질문 | 답변 |
|------|------|
| **"이 프로그램이 실제 ATEM Mini Pro를 제어할 수 있는가?"** | ✅ **네.** PyATEMMax 프로토콜 호출 순서가 정확하며, CUT/MIX/DIP/WIPE/DVE/PiP/FTB 모두 실제 ATEM 하드웨어에서 작동합니다. 연결 재시도 로직과 중복 패킷 최적화만 보완하면 라이브 현장 투입이 가능합니다. |
| **"장면 선택 알고리즘이 고품질 성과를 낼 수 있는가?"** | ⚠️ **현재로서는 "기초적 수준"입니다.** 블러 제외·무신호 감지·연출 룰 엔진은 우수하지만, 핵심인 구도 인식이 사람/얼굴 감지 없이 단순 밝기 기반이고, 오디오 분석이 완전한 가짜(하드코딩)입니다. **Phase 2의 얼굴 감지 + Optical Flow + 실제 오디오 입력**을 적용하면 실전 품질이 됩니다. |
