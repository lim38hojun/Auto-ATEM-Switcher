---
name: direction-rule-engine
description: >-
  Configures, debugs, and tunes the Broadcast Direction State Machine (src/engine/state_machine.py) and Audio-driven triggers (src/pipeline/audio_analyzer.py). Use this skill when tuning minimum shot hold times, maximum shot rotation timers, anti-flicker hysteresis, beat-synchronized cutting, or solo camera weight boosting.
---

# Broadcast Direction Rule Engine Skill

이 스킬은 카메라 점수만을 단순 비교할 때 발생하는 0.5초 단위의 화면 깜빡임(Flicker)을 방지하고, 실제 방송 PD의 연출 휴리스틱(최소 유지 시간, 최대 전환 시간, 음악 비트 동기화, 솔로 카메라 가중치)을 적용할 때 사용합니다.

## 1. 상태 기계(State Machine) 4단계 동작 원리

`BroadcastStateMachine`은 매 프레임 다음 4가지 상태 중 하나를 가집니다:

1. **`HOLD_LOCKED` (`0.0s <= elapsed < min_hold_sec`)**:
   - 직전 컷 전환 이후 `min_hold_sec`(기본 `2.5초`)가 지나지 않은 상태입니다.
   - 다른 카메라 점수가 아무리 높아도 전환하지 않습니다.
   - **예외**: 현재 Program 카메라에 연속 블러/흔들림(`is_blurry=True`)이 발생하면 즉시 `EMERGENCY_CUT`을 실행합니다.
2. **`ELIGIBLE` (`min_hold_sec <= elapsed < max_hold_sec`)**:
   - 최고 점수 카메라(`best_cam`)가 현재 카메라(`program_cam`)와 다르고, 점수 차이가 `switch_margin`(기본 `0.10`) 이상이면 전환 후보(`Preview`)로 올립니다.
   - `beat_sync_enabled=True`인 경우 `BEAT_ARMED` 상태로 전이하여 직후 도래하는 드럼 킥/스네어 비트 피크에 맞춰 컷 전환합니다.
3. **`BEAT_ARMED`**:
   - 전환이 결정된 상태에서 음악의 박자(`is_beat_peak=True`)를 기다리는 대기 상태입니다. 최대 `0.45초` 내에 비트가 감지되면 즉시 박자에 맞춰 전환하고, 초과 시 타임아웃 전환합니다.
4. **`FORCED_ROTATION` (`elapsed >= max_hold_sec`)**:
   - 한 카메라가 `max_hold_sec`(기본 `7.5초`) 이상 고정되어 지루함을 유발할 때 발동합니다.
   - 현재 카메라를 **제외한** 나머지 정상 카메라 중 최고 점수 카메라로 즉시 전환합니다.

## 2. 검증 방법

룰 엔진 파라미터 변경 후 아래 테스트를 실행하여 최소/최대 유지 시간 불변식을 검증하십시오:

```powershell
pytest tests/test_state_machine.py -v
```
