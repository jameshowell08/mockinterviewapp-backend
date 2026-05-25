from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class User(Base):
    __tablename__ = "users"

    email = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=True)

    interviews = relationship("Interview", back_populates="user")


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, ForeignKey("users.email"))
    job_role = Column(String)
    difficulty = Column(String)
    language = Column(String, default="en")
    
    # Analysis results
    summary = Column(Text, nullable=True)
    rating = Column(Integer, nullable=True)
    strengths = Column(Text, nullable=True) # Stored as JSON string
    improvements = Column(Text, nullable=True) # Stored as JSON string
    
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="interviews")
    transcripts = relationship("TranscriptLine", back_populates="interview")


class TranscriptLine(Base):
    __tablename__ = "transcript_lines"

    id = Column(Integer, primary_key=True, index=True)
    interview_id = Column(Integer, ForeignKey("interviews.id"))
    role = Column(String) # "interviewer" or "user"
    text = Column(Text)
    
    interview = relationship("Interview", back_populates="transcripts")
