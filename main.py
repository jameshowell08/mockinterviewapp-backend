import os
import json
import asyncio
import base64
import websockets
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import get_db, engine, Base
import models
# pyrefly: ignore [missing-import]
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

load_dotenv(override=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup: create DB tables safely (won't crash the app if DB is unreachable) ──
@app.on_event("startup")
async def startup_event():
    try:
        Base.metadata.create_all(bind=engine)
        print("[DB] Tables created/verified successfully")
    except Exception as e:
        print(f"[DB] Warning: Could not create tables at startup: {e}")


# Fetch API keys without crashing if missing
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-flash-live-preview"
GEMINI_REST_MODEL = "gemini-3.1-flash"

GEMINI_WS_URL = (
    f"wss://generativelanguage.googleapis.com/ws/"
    f"google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    f"?key={GEMINI_API_KEY}"
)

GEMINI_REST_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_REST_MODEL}:generateContent"


# ──────────────────────────────────────────────
# System instruction builder
# ──────────────────────────────────────────────

LANGUAGE_CONFIG = {
    "en": {"code": "en-US", "name": "English", "instruction": "Conduct the entire interview in English. The user is speaking English. Use the 'en-US' language code for transcription."},
    "id": {"code": "id-ID", "name": "Indonesian", "instruction": "Conduct the entire interview in Indonesian (Bahasa Indonesia). The user is speaking Indonesian. Use the 'id-ID' language code for transcription."},
}

DIFFICULTY_PROMPTS = {
    "easy": (
        "Ask beginner-friendly questions. Be encouraging and give hints when "
        "the candidate struggles. Focus on fundamental concepts and basic scenarios."
    ),
    "normal": (
        "Ask standard industry-level interview questions. Expect solid answers "
        "with some depth. Ask follow-up questions to probe understanding."
    ),
    "hard": (
        "Ask advanced, challenging questions that test deep expertise. Include "
        "system design, edge cases, and complex problem-solving. Be rigorous "
        "and expect detailed, well-structured answers."
    ),
}

ROLE_DESCRIPTIONS = {
    "Software Engineer": "software engineering, including data structures, algorithms, system design, coding best practices, and software architecture",
    "Product Manager": "product management, including product strategy, user research, metrics, prioritization frameworks, stakeholder management, and go-to-market",
    "QA Engineer": "quality assurance engineering, including test strategy, test automation, bug tracking, CI/CD testing, performance testing, and quality metrics",
    "Data Scientist": "data science, including statistics, machine learning, data analysis, feature engineering, model evaluation, and data visualization",
    "DevOps Engineer": "DevOps engineering, including CI/CD pipelines, infrastructure as code, containerization, cloud services, monitoring, and incident response",
    "UI/UX Designer": "UI/UX design, including user research, wireframing, prototyping, design systems, usability testing, and accessibility",
}


def build_system_instruction(job_role: str, difficulty: str, language: str = "en") -> str:
    role_desc = ROLE_DESCRIPTIONS.get(job_role, ROLE_DESCRIPTIONS["Software Engineer"])
    diff_prompt = DIFFICULTY_PROMPTS.get(difficulty, DIFFICULTY_PROMPTS["normal"])
    lang_config = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["en"])

    return (
        f"You are an expert interviewer conducting a mock job interview for a "
        f"{job_role} position. Your expertise covers {role_desc}.\n\n"
        f"LANGUAGE: {lang_config['instruction']}\n\n"
        f"Interview difficulty: {difficulty.upper()}.\n"
        f"{diff_prompt}\n\n"
        f"RULES:\n"
        f"1. Start by warmly greeting the candidate and immediately asking them to introduce themselves and tell you a bit about their background.\n"
        f"2. Ask ONE question at a time. Always wait for the candidate to finish before asking the next.\n"
        f"3. Be highly flexible and natural. Instead of just reading from a list, listen to the candidate's answers and ask detailed, probing follow-up questions based specifically on what they just said.\n"
        f"4. Keep your responses conversational and concise (1-3 sentences typically) so the interview flows like a real chat.\n"
        f"5. Do NOT use markdown formatting in your responses.\n"
        f"6. If the candidate interrupts you, stop immediately and listen.\n"
        f"7. After about 5-7 questions, wrap up the interview naturally.\n"
        f"8. Be professional but friendly, acting exactly like a real human interviewer."
    )


# ──────────────────────────────────────────────
# WebSocket interview endpoint
# ──────────────────────────────────────────────

@app.websocket("/ws/interview")
async def websocket_interview(client_ws: WebSocket):
    """
    Bidirectional audio streaming proxy between the browser and the
    Gemini Live API. Streams raw PCM audio in both directions and
    forwards transcription events.
    """
    await client_ws.accept()

    if not GEMINI_API_KEY:
        await client_ws.send_json({"type": "error", "message": "Gemini API key not configured"})
        await client_ws.close()
        return

    gemini_ws = None
    transcript_log: List[dict] = []

    try:
        # Wait for the config message from the client
        config_raw = await client_ws.receive_text()
        config = json.loads(config_raw)
        job_role = config.get("jobRole", "Software Engineer")
        difficulty = config.get("difficulty", "normal")
        language = config.get("language", "en")

        print(f"[Session] Starting interview: {job_role} / {difficulty} / {language}")

        system_instruction = build_system_instruction(job_role, difficulty, language)

        setup_message = {
            "setup": {
                "model": f"models/{GEMINI_MODEL}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Puck"
                            }
                        }
                    }
                },
                "systemInstruction": {
                    "parts": [{"text": system_instruction}]
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }
        }

        # Connect to Gemini WebSocket
        gemini_ws = await websockets.connect(
            GEMINI_WS_URL,
            extra_headers={"Content-Type": "application/json"},
        )
        print("[Gemini] WebSocket connected")

        await gemini_ws.send(json.dumps(setup_message))
        print(f"[Gemini] Setup sent for model: {GEMINI_MODEL}")

        # Wait for setup complete
        setup_response_raw = await gemini_ws.recv()
        setup_response = json.loads(setup_response_raw)
        print(f"[Gemini] Setup response: {json.dumps(setup_response)[:200]}")

        await client_ws.send_json({"type": "status", "status": "live"})

        greeting_msg = {
            "realtimeInput": {
                "text": "Please start the interview. Greet me and ask your first question."
            }
        }
        await gemini_ws.send(json.dumps(greeting_msg))
        print("[Gemini] Sent greeting prompt")

        # ── Bidirectional streaming ──

        async def client_to_gemini():
            """Forward audio and control messages from browser to Gemini."""
            try:
                while True:
                    data = await client_ws.receive_text()
                    msg = json.loads(data)
                    msg_type = msg.get("type", "")

                    if msg_type == "audio":
                        audio_msg = {
                            "realtimeInput": {
                                "audio": {
                                    "data": msg["data"],
                                    "mimeType": "audio/pcm;rate=16000"
                                }
                            }
                        }
                        await gemini_ws.send(json.dumps(audio_msg))

                    elif msg_type == "text":
                        text_msg = {
                            "realtimeInput": {
                                "text": msg.get("text", "")
                            }
                        }
                        await gemini_ws.send(json.dumps(text_msg))
                        print(f"[Client -> Gemini] Text: {msg.get('text', '')[:80]}")

            except WebSocketDisconnect:
                print("[Client] Disconnected")
            except Exception as e:
                print(f"[client_to_gemini error] {e}")

        async def gemini_to_client():
            """Forward audio, transcriptions, and control from Gemini to browser."""
            try:
                async for raw_message in gemini_ws:
                    response = json.loads(raw_message)

                    server_content = response.get("serverContent")
                    if server_content:
                        model_turn = server_content.get("modelTurn")
                        turn_complete = server_content.get("turnComplete", False)
                        interrupted = server_content.get("interrupted", False)

                        if model_turn and "parts" in model_turn:
                            for part in model_turn["parts"]:
                                if "inlineData" in part:
                                    inline = part["inlineData"]
                                    await client_ws.send_json({
                                        "type": "audio",
                                        "data": inline.get("data", ""),
                                        "mimeType": inline.get("mimeType", "audio/pcm;rate=24000")
                                    })
                                elif "text" in part:
                                    await client_ws.send_json({
                                        "type": "text",
                                        "text": part["text"]
                                    })

                        input_transcription = server_content.get("inputTranscription")
                        if input_transcription:
                            text = input_transcription.get("text", "")
                            finished = input_transcription.get("finished", False)
                            if text:
                                await client_ws.send_json({
                                    "type": "input_transcription",
                                    "text": text,
                                    "finished": finished
                                })
                                if finished:
                                    transcript_log.append({"role": "user", "text": text})

                        output_transcription = server_content.get("outputTranscription")
                        if output_transcription:
                            text = output_transcription.get("text", "")
                            finished = output_transcription.get("finished", False)
                            if text:
                                await client_ws.send_json({
                                    "type": "output_transcription",
                                    "text": text,
                                    "finished": finished
                                })
                                if finished:
                                    transcript_log.append({"role": "interviewer", "text": text})

                        if interrupted:
                            await client_ws.send_json({"type": "interrupted"})
                            print("[Gemini] Interrupted by user")

                        if turn_complete:
                            await client_ws.send_json({"type": "turn_complete"})

                    if "setupComplete" in response:
                        print("[Gemini] Setup complete event")

            except websockets.exceptions.ConnectionClosedOK:
                print("[Gemini] Connection closed normally")
            except websockets.exceptions.ConnectionClosedError as e:
                print(f"[Gemini] Connection closed with error: {e}")
            except Exception as e:
                print(f"[gemini_to_client error] {e}")

        await asyncio.gather(
            client_to_gemini(),
            gemini_to_client(),
        )

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"[Gemini] Failed to connect: {e}")
        try:
            await client_ws.send_json({"type": "error", "message": f"Failed to connect to Gemini: {e}"})
        except Exception:
            pass
    except Exception as e:
        print(f"[WebSocket Error] {e}")
        try:
            await client_ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if gemini_ws and not gemini_ws.closed:
            await gemini_ws.close()
        try:
            if transcript_log:
                await client_ws.send_json({
                    "type": "transcript_log",
                    "data": transcript_log
                })
        except Exception:
            pass
        print(f"[Session] Cleaned up. Transcript entries: {len(transcript_log)}")


# ──────────────────────────────────────────────
# Summary endpoint (REST)
# ──────────────────────────────────────────────

class SummaryRequest(BaseModel):
    userEmail: str
    jobRole: str
    difficulty: str
    transcript: List[dict]


class SummaryResponse(BaseModel):
    summary: str
    rating: int
    strengths: List[str]
    improvements: List[str]


@app.post("/api/summary")
async def generate_summary(request: SummaryRequest, db: Session = Depends(get_db)):
    """Generate an interview summary and rating from the transcript."""
    if not GEMINI_API_KEY:
        return {"error": "API key not configured"}

    transcript_text = "\n".join(
        f"{'Interviewer' if t.get('role') == 'interviewer' else 'Candidate'}: {t.get('text', '')}"
        for t in request.transcript
    )

    prompt = (
        f"You are an expert interview coach. Analyze this mock interview transcript "
        f"for a {request.jobRole} position at {request.difficulty} difficulty.\n\n"
        f"TRANSCRIPT:\n{transcript_text}\n\n"
        f"Provide your analysis in EXACTLY this JSON format (no markdown, no code fences):\n"
        f'{{\n'
        f'  "summary": "A 2-3 paragraph overall assessment of the interview performance",\n'
        f'  "rating": <number from 1-10>,\n'
        f'  "strengths": ["strength 1", "strength 2", "strength 3"],\n'
        f'  "improvements": ["area 1", "area 2", "area 3"]\n'
        f'}}\n\n'
        f"Be specific and reference actual answers from the transcript."
    )

    url = f"{GEMINI_REST_URL}?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json"
        }
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()

        text = result["candidates"][0]["content"]["parts"][0]["text"]
        analysis = json.loads(text)

        try:
            user = db.query(models.User).filter(models.User.email == request.userEmail).first()
            if not user:
                user = models.User(email=request.userEmail)
                db.add(user)
                db.commit()

            interview = models.Interview(
                user_email=request.userEmail,
                job_role=request.jobRole,
                difficulty=request.difficulty,
                summary=analysis.get("summary", ""),
                rating=analysis.get("rating", 0),
                strengths=json.dumps(analysis.get("strengths", [])),
                improvements=json.dumps(analysis.get("improvements", []))
            )
            db.add(interview)
            db.commit()
            db.refresh(interview)

            for t in request.transcript:
                line = models.TranscriptLine(
                    interview_id=interview.id,
                    role=t.get("role", "unknown"),
                    text=t.get("text", "")
                )
                db.add(line)
            db.commit()
        except Exception as db_err:
            print(f"[Database Error] {db_err}")

        return analysis

    except Exception as e:
        print(f"[Summary Error] {e}")
        return {
            "summary": "Unable to generate summary. Please try again.",
            "rating": 5,
            "strengths": ["Interview completed"],
            "improvements": ["Try again for a detailed analysis"]
        }


@app.get("/api/history")
def get_history(email: str, db: Session = Depends(get_db)):
    interviews = (
        db.query(models.Interview)
        .filter(models.Interview.user_email == email)
        .order_by(models.Interview.created_at.desc())
        .all()
    )
    return [{
        "id": i.id,
        "job_role": i.job_role,
        "difficulty": i.difficulty,
        "rating": i.rating,
        "created_at": i.created_at
    } for i in interviews]


@app.get("/api/history/{interview_id}")
def get_interview_detail(interview_id: int, db: Session = Depends(get_db)):
    interview = db.query(models.Interview).filter(models.Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    transcripts = db.query(models.TranscriptLine).filter(
        models.TranscriptLine.interview_id == interview_id
    ).all()

    return {
        "id": interview.id,
        "job_role": interview.job_role,
        "difficulty": interview.difficulty,
        "summary": interview.summary,
        "rating": interview.rating,
        "strengths": json.loads(interview.strengths) if interview.strengths else [],
        "improvements": json.loads(interview.improvements) if interview.improvements else [],
        "created_at": interview.created_at,
        "transcripts": [{"role": t.role, "text": t.text} for t in transcripts]
    }


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
def register_user(request: RegisterRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == request.email).first()
    if user:
        if user.hashed_password:
            raise HTTPException(status_code=400, detail="Email already registered")
        else:
            user.hashed_password = pwd_context.hash(request.password)
            user.name = request.name
            db.commit()
            return {"email": user.email, "name": user.name}

    new_user = models.User(
        email=request.email,
        name=request.name,
        hashed_password=pwd_context.hash(request.password)
    )
    db.add(new_user)
    db.commit()
    return {"email": new_user.email, "name": new_user.name}


@app.post("/api/auth/login")
def login_user(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == request.email).first()
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not pwd_context.verify(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return {"email": user.email, "name": user.name}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)