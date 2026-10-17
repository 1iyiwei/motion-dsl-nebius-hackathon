# Token Factory / NVIDIA feedback log

Running notes for the submissions' required feedback section (and the Most Valuable Feedback prize).
Add an entry when something surprises us. Include the date, what happened, and the impact.

## 2026-10-17

- **Model catalog** (`GET /v1/models`, 28 models): NVIDIA models available are Nemotron-3 Nano 30B-A3B,
  Super 120B-A12B, Ultra 550B-A55B, Nemotron-3.5 Super-VL, and Nemotron-3.5 Lightning. The catalog web page
  requires login, so `/v1/models` is the only way to get exact ids from a script. Good, but the docs don't list ids.
- **Reasoning toggle**: `chat_template_kwargs: {"enable_thinking": false}` on Nano cut a small JSON edit from
  3.3 s / 630 tokens to 1.1 s / 114 tokens with the same answer. Putting `/no_think` in the system prompt instead
  made Nano spin to `max_tokens` (4000) and return empty content after 17 s. The toggle isn't in the Token Factory
  docs as far as we found. Documenting it per model family would save people time.
- **JSON mode** (`response_format: json_object`) worked on all five NVIDIA models on the first try. Reasoning
  arrives separately (`reasoning_content`), so `content` is clean JSON. 👍
- **Latency** (Ultra, ~3k-token prompt, JSON reply): 2–5 s typical for a full document, but repairs with long
  reasoning went up to 30 s / 10k tokens. A per-request reasoning budget would help interactive apps.
- **Sandboxes** is in Beta behind an access request. Access requested: <date/status here>.
