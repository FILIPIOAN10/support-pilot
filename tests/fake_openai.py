"""A tiny HTTP server that speaks the part of the OpenAI chat API the agent uses, INCLUDING tool calls.
The real ChatOpenAI class talks to it, so the tests prove that our tools turn into valid OpenAI tool schemas,
that tool_calls in a response are understood, and that the agent loop works end to end - with no key and no bill."""

import json
import time

from fastapi import FastAPI, Request

app = FastAPI()
requests_seen: list[dict] = []


def _completion(body: dict, message: dict, finish: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "refusal": None, **message},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    requests_seen.append(body)
    last = body["messages"][-1]
    if last["role"] == "tool":
        return _completion(body, {"content": f"The system says: {last['content']}"}, "stop")
    text = str(last["content"])
    if "order" in text.lower():
        call = {
            "id": "call_abc123",
            "type": "function",
            "function": {
                "name": "get_order",
                "arguments": json.dumps({"order_id": "1002"}),
            },
        }
        return _completion(body, {"content": None, "tool_calls": [call]}, "tool_calls")
    return _completion(body, {"content": "Hello! How can I help?"}, "stop")
