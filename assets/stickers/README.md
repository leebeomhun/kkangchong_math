# 파스텔 친구 스티커

## 사진 속 아이와 토끼인형 스티커 6종 추가

- 생성: 2026-11-05, OpenAI 내장 `image_gen` 도구
- 위치: `photo-friends-v1/` (개별 1254×1254 RGBA 투명 PNG)
- 참고: `assets/image/KakaoTalk_Photo_2026-11-05-15-11-57.png`
- 특징: 양갈래 머리와 연보라색 옷, 아이보리 토끼인형의 꽃무늬 긴 귀와 분홍 코를 반영한 그림
- 구성: 안아주기(`hug-v1.png`), 브이(`peace-v1.png`), 함께 공부(`reading-v1.png`), 별 선물(`star-v1.png`), 당근 선물(`carrot-v1.png`), 응원(`cheer-v1.png`)
- 전체 프롬프트: `photo-friends-v1/generation-prompts.txt`. 각 파일별 실제 생성 프롬프트를 보관합니다. 두 번째 이미지부터는 첫 안아주기 스티커도 그림체 참고로 사용했습니다.
- 저장 키: `friend-hug`, `friend-peace`, `friend-reading`, `friend-star`, `friend-carrot`, `friend-cheer`. 서버의 허용 목록에도 등록합니다.
- 기존 30종과 저장 키를 유지하며, 새 6종을 더해 총 36종입니다. 새 그림은 개별 `<img>`로 표시하고 서비스 워커가 사전 캐시합니다. 원본 이미지의 배경 제거·자르기 후처리는 하지 않았습니다.

## 기존 30종

- 생성: 2026-11-05, OpenAI 내장 `image_gen` 도구
- 원본: `pastel-friends-v1.png` (1145×1374, RGBA 투명 PNG, 30종)
- 전체 생성 프롬프트: `generation-prompt.txt`
- 사용: `index.html`의 `stickerArt()`가 같은 PNG에서 각 그림 영역을 SVG `viewBox`로 표시합니다. 원본 파일을 자르거나 배경을 지우는 후처리는 하지 않았습니다.
- 순서: 기존 `STICKERS`와 같은 순서이며, 이모지는 브라우저 저장 기록의 키로 계속 사용합니다.
- 오프라인: `sw.js`의 사전 캐시 목록에 원본을 포함합니다.

1행: 강아지, 고양이, 토끼, 여우, 곰  
2행: 판다, 코알라, 호랑이, 사자, 돼지  
3행: 개구리, 펭귄, 병아리, 유니콘, 고래  
4행: 돌고래, 나비, 무당벌레, 무지개, 벚꽃  
5행: 해바라기, 딸기, 체리, 케이크, 컵케이크  
6행: 막대사탕, 아이스크림, 리본, 왕관, 보석
