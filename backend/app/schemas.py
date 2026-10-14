from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict

Phase = Literal["Define", "Measure", "Analyze", "Improve", "Control"]

class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    process_name: str = Field(default="", max_length=160)
    problem_statement: str = Field(default="", max_length=4000)
    goal_statement: str = Field(default="", max_length=4000)
    owner: str = Field(default="", max_length=160)
    priority: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    target_date: str = Field(default="", max_length=40)

class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=2, max_length=160)
    process_name: str | None = Field(default=None, max_length=160)
    problem_statement: str | None = Field(default=None, max_length=4000)
    goal_statement: str | None = Field(default=None, max_length=4000)
    owner: str | None = Field(default=None, max_length=160)
    status: Phase | Literal["Completed", "On Hold"] | None = None
    priority: Literal["Low", "Medium", "High", "Critical"] | None = None
    target_date: str | None = Field(default=None, max_length=40)

class EntryCreate(BaseModel):
    phase: Phase
    entry_type: Literal["note", "decision", "action", "risk", "metric", "hypothesis"] = "note"
    title: str = Field(min_length=2, max_length=240)
    content: str = Field(min_length=1, max_length=12000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)

class AnalysisRequest(BaseModel):
    value_column: str
    lsl: float | None = None
    usl: float | None = None
    defective_column: str | None = None
    defect_count_column: str | None = None
    opportunities_per_unit: int | None = Field(default=None, ge=1, le=1000000)
    group_column: str | None = None
    group_a: str | None = None
    group_b: str | None = None

class AgentRequest(BaseModel):
    project_id: str | None = None
    dataset_id: str | None = None
    analysis_run_id: str | None = None
    prompt: str = Field(default="Analyze the process and recommend evidence-based next steps.", max_length=6000)

class AgentResumeRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=6000)

class HealthResponse(BaseModel):
    status: str
    version: str
    llm_configured: bool
    database: str

class MessageResponse(BaseModel):
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class ManualCSVRequest(BaseModel):
    filename: str = Field(default="manual-data.csv", max_length=180)
    csv_text: str = Field(min_length=3, max_length=5_000_000)

class HTTPJSONConnectorRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    bearer_token: str = Field(default="", max_length=4000)
    filename: str = Field(default="connected-source.csv", max_length=180)

class TeamReviewRequest(BaseModel):
    project_id: str | None = None
    dataset_id: str | None = None
    analysis_run_id: str | None = None
    question: str = Field(default="Review this process as a cross-functional Six Sigma team and create the most useful next-step plan.", max_length=6000)

class TeamActionUpdate(BaseModel):
    status: Literal["not_started", "in_progress", "blocked", "completed"]
    note: str = Field(default="", max_length=2000)
