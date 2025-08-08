import requests
import json

API_KEY = "app-ExZuBglMcJnH7LImAa5EU3Vh"
url = "https://api.dify.ai/v1/chat-messages"
payload = {
    "inputs": {},
    "query": "What is my new guest growth rate?",
    "response_mode": "streaming",
    "user": "user-id-123",
    # "conversation_id": ""
}

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# response = requests.post(url, json=payload, headers=headers)
# data = response.json()

# # 🔽 Extract values
# final_answer = data.get("answer") or data.get("outputs", {}).get("response")
# conversation_id = data.get("conversation_id")

# print("🗣 Final response:", final_answer)
# print("💬 Conversation ID:", conversation_id)

# if final_answer.startswith("MD:"):
#     print(final_answer.lstrip("MD:\n"))
# if final_answer.startswith("Python:"):
#     exec(final_answer.lstrip("Python:\n```python\n").rstrip("```"))

# 🔁 Enable stream
response = requests.post(url, json=payload, headers=headers, stream=True)

final_answer = ""
conversation_id = None

print("🗣 Streaming response:")
for line in response.iter_lines():
    if not line or not line.startswith(b"data:"):
        continue

    try:
        # Remove "data: " prefix and decode
        event_data = json.loads(line[len(b"data: "):])

        # Print streamed content
        if "answer" in event_data:
            content = event_data["answer"]
            final_answer += content
            print(content, end="", flush=True)  # Stream it to console

        # Save conversation_id if present
        if "conversation_id" in event_data:
            conversation_id = event_data["conversation_id"]

        # End of stream
        if event_data.get("event") == "message_end":
            break

    except json.JSONDecodeError:
        continue

print("\n💬 Conversation ID:", conversation_id)