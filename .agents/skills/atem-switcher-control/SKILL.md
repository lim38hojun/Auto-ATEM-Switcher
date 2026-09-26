---
name: atem-switcher-control
description: >-
  Manages ATEM Mini Pro switcher connectivity and command execution via PyATEMMax, Bitfocus Companion HTTP webhooks, or MockATEMSwitcher simulation. Use this skill when configuring hardware IP connections, debugging Program/Preview tally states, or setting up transition effects (Cut, Mix/Auto) in src/atem/controller.py.
---

# ATEM Switcher Control & Simulation Skill

이 스킬은 AI 방송 디렉터가 선정한 카메라 번호(`1~4`)를 실제 **Blackmagic Design ATEM Mini Pro** 하드웨어 또는 테스트용 **가상 스위처(`MockATEMSwitcher`)**로 전송할 때 사용합니다.

## 1. 지원 제어 어댑터 (`src/atem/controller.py`)

1. **`MockATEMSwitcher` (기본값, 하드웨어 불필요)**:
   - 실제 ATEM 장비 없이도 PC 단독으로 모든 기능을 테스트할 수 있도록 Program 버스, Preview 버스, 전환 효과(`CUT` / `AUTO MIX`), Tally 상태(Red/Green), 명령 히스토리 타임라인을 메모리에서 완벽히 에뮬레이션합니다.
2. **`PyATEMMaxAdapter` (Python UDP 직접 제어)**:
   - ATEM Mini Pro와 이더넷(유선 LAN)으로 동일 대역(예: `192.168.10.240`)에 연결된 경우 사용합니다.
   - 주요 호출 API:
     ```python
     switcher.setPreviewInputVideoSource(0, preview_cam_id)
     switcher.setProgramInputVideoSource(0, program_cam_id) # 즉시 Cut 또는
     switcher.execCutME(0)  # Preview <-> Program 컷 전환
     switcher.execAutoME(0) # Mix/Dip 전환 효과 실행
     ```
3. **`CompanionWebhookAdapter` (Bitfocus Companion REST 연동)**:
   - 로컬에서 구동 중인 Bitfocus Companion(`http://127.0.0.1:8000/api/location/<page>/<bank>/<button>/press`)으로 HTTP POST 요청을 보내 매크로/스위칭을 트리거합니다.

## 2. 연결 상태 점검

서버 기동 중 `/api/atem/status` 엔드포인트를 호출하면 현재 연결 모드(`mock` | `pyatemmax` | `companion`), 현재 Program/Preview 카메라 번호, 누적 전환 횟수를 확인할 수 있습니다.
