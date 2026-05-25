import asyncio
import websockets
import json
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-3.1-flash-live-preview"
URL = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={API_KEY}"

setup = {
    "setup": {
        "model": f"models/{MODEL}",
        "generationConfig": {
            "responseModalities": ["AUDIO"]
        },
        "systemInstruction": {
            "parts": [{"text": "You are a friendly interviewer. Keep responses very short."}]
        },
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
    }
}

# Try realtimeInput.text instead of clientContent
greeting = {
    "realtimeInput": {
        "text": "Hello, please greet me briefly."
    }
}

async def test():
    print(f"Model: models/{MODEL}")
    try:
        ws = await websockets.connect(URL, extra_headers={"Content-Type": "application/json"})
        print("Connected!")
        await ws.send(json.dumps(setup))
        resp = await asyncio.wait_for(ws.recv(), timeout=10)
        print(f"Setup: {resp[:200]}")

        await ws.send(json.dumps(greeting))
        print("Greeting sent via realtimeInput.text. Waiting...")

        for i in range(30):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                data = json.loads(msg)
                if "serverContent" in data:
                    sc = data["serverContent"]
                    if sc.get("modelTurn", {}).get("parts"):
                        for p in sc["modelTurn"]["parts"]:
                            if "inlineData" in p:
                                print(f"  [{i}] AUDIO chunk (len={len(p['inlineData'].get('data',''))})")
                            elif "text" in p:
                                print(f"  [{i}] TEXT: {p['text'][:100]}")
                    if sc.get("outputTranscription"):
                        print(f"  [{i}] OUTPUT_TRANSCRIPTION: {sc['outputTranscription'].get('text','')[:100]}")
                    if sc.get("turnComplete"):
                        print(f"  [{i}] TURN_COMPLETE")
                        break
                else:
                    print(f"  [{i}] OTHER: {json.dumps(data)[:200]}")
            except asyncio.TimeoutError:
                print(f"  [{i}] (timeout)")

        await ws.close()
        print("Done!")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")

asyncio.run(test())
