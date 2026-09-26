# Broadcast Direction State Machine Rules

이 규칙은 `src/engine/state_machine.py`의 방송 연출 룰 엔진을 구현·수정할 때 적용되는 필수 제약 사항입니다.

## 1. 핵심 연출 휴리스틱 (Broadcast Heuristics)

1. **최소 샷 유지 시간 (`min_hold_sec`, 기본값 `2.5`초, 허용 범위 `1.5 ~ 5.0`초)**:
   - 카메라 전환 직후 최소 `min_hold_sec`가 경과하기 전에는 다른 카메라의 점수가 더 높더라도 전환을 억제(`HOLD_LOCKED` 상태 유지)합니다.
   - **유일한 예외 (Emergency Blur Evasion)**: 현재 송출 중인 `Program` 카메라에서 심각한 블러/흔들림(`is_blurry == True`가 연속 3프레임 이상 지속)이 감지되면 시청자 피로 방지를 위해 최소 유지 시간과 무관하게 즉시 차선책 정상 카메라로 비상 전환(`EMERGENCY_CUT`)합니다.
2. **최대 샷 유지 제한 시간 (`max_hold_sec`, 기본값 `7.5`초, 허용 범위 `5.0 ~ 15.0`초)**:
   - 단일 카메라가 `max_hold_sec`를 초과하여 계속 송출되면 방송 화면이 지루해지므로 `FORCED_ROTATION` 상태로 진입합니다.
   - 이때 **현재 Program 카메라를 제외한 나머지 정상 카메라(`is_blurry == False`) 중 최고 점수 카메라**를 선정하여 전환합니다.
3. **히스테리시스 전환 마진 (`switch_margin`, 기본값 `0.10`)**:
   - 일반 전환 가능 구간(`min_hold_sec <= duration < max_hold_sec`)에서는 후보 카메라의 점수 $S_{\text{candidate}}$가 현재 카메라 점수 $S_{\text{current}} + \text{switch\_margin}$ 이상일 때만 전환을 승인합니다.
4. **오디오 비트 피크 동기화 (`beat_sync_enabled`, 기본값 `True`)**:
   - 드럼 킥/스네어 비트 피크(`is_beat_peak == True`)가 감지되면 전환 마진을 완화하거나 대기 중이던 프리뷰(`Preview`) 카메라를 박자에 맞춰 즉시 `Program`으로 컷(Cut) 전환합니다.
   - 만약 후보 선정 후 `beat_wait_window_sec`(기본 `0.45`초) 내에 비트가 감지되지 않으면 지연 방지를 위해 즉시 전환을 실행합니다.
