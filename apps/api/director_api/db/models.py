import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Identity, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from director_api.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="00000000-0000-0000-0000-000000000001")

    title: Mapped[str] = mapped_column(String(500))
    topic: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    target_runtime_minutes: Mapped[int] = mapped_column(Integer)

    audience: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tone: Mapped[str | None] = mapped_column(String(500), nullable=True)
    visual_style: Mapped[str | None] = mapped_column(String(500), nullable=True)
    narration_style: Mapped[str | None] = mapped_column(String(500), nullable=True)
    factual_strictness: Mapped[str | None] = mapped_column(String(32), nullable=True)
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    music_preference: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Picture geometry for all generated stills, scene clips, and exports: "16:9" (default) or "9:16".
    frame_aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9", server_default="16:9")

    preferred_text_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_image_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_video_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_speech_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)

    workflow_phase: Mapped[str] = mapped_column(String(64), default="draft")
    director_output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    critic_policy_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    research_min_sources: Mapped[int] = mapped_column(Integer, default=3)

    # When True, storyboard sync / export auto-heal build one timeline clip per export-ready approved
    # visual per scene (ordered by gallery sequence), not only a single primary still per scene.
    use_all_approved_scene_media: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chapters: Mapped[list["Chapter"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    research_dossiers: Mapped[list["ResearchDossier"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    characters: Mapped[list["ProjectCharacter"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlatformStripeSettings(Base):
    """Singleton row ``id=1``: platform Stripe keys and billing URLs. Non-empty values override env."""

    __tablename__ = "platform_stripe_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    stripe_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_webhook_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_publishable_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_success_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_cancel_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_price_studio_monthly: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    slug: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    firebase_uid: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubscriptionPlan(Base):
    """Admin-configurable product: Stripe price linkage + feature entitlements (JSON).

    Future admin UI can CRUD rows; checkout and webhooks resolve access from here.
    """

    __tablename__ = "subscription_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    stripe_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    billing_interval: Mapped[str] = mapped_column(String(32), default="month")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    entitlements_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BillingPaymentEvent(Base):
    """Stripe webhook audit trail (idempotent on stripe_event_id)."""

    __tablename__ = "billing_payment_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stripe_event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    stripe_object_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    livemode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payload_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantBilling(Base):
    """Per-workspace Stripe subscription state; entitlements come from plan + optional override."""

    __tablename__ = "tenant_billing"

    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="none")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entitlements_override_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlmPromptDefinition(Base):
    """Global catalog of editable LLM system prompts (defaults for all workspaces)."""

    __tablename__ = "llm_prompt_definitions"

    prompt_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_content: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserLlmPromptOverride(Base):
    """Per-user (or workspace-anonymous) override of a prompt within a tenant."""

    __tablename__ = "user_llm_prompt_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    prompt_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("llm_prompt_definitions.prompt_key", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserNarrationStyle(Base):
    """Custom narration voice brief (LLM) for a user within a tenant."""

    __tablename__ = "user_narration_styles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    prompt_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    started_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    steps_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    block_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    block_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pipeline_options_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # UI/worker: { "paused": bool, "stop_requested": bool }
    pipeline_control_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="agent_runs")


class ProjectCharacter(Base):
    """Editable visual bible per project — used for image/video consistency."""

    __tablename__ = "project_characters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(256))
    role_in_story: Mapped[str] = mapped_column(Text)
    visual_description: Mapped[str] = mapped_column(Text)
    time_place_scope_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="characters")


class ResearchDossier(Base):
    __tablename__ = "research_dossiers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending_review")
    body_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    override_actor_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_ticket_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="research_dossiers")
    sources: Mapped[list["ResearchSource"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan"
    )
    claims: Mapped[list["ResearchClaim"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan"
    )


class ResearchSource(Base):
    __tablename__ = "research_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    dossier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_dossiers.id", ondelete="CASCADE"), index=True)
    url_or_reference: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    extracted_facts_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dossier: Mapped["ResearchDossier"] = relationship(back_populates="sources")


class ResearchClaim(Base):
    __tablename__ = "research_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    dossier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_dossiers.id", ondelete="CASCADE"), index=True)
    claim_text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    adequately_sourced: Mapped[bool] = mapped_column(Boolean, default=False)
    source_refs_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dossier: Mapped["ResearchDossier"] = relationship(back_populates="claims")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    critic_gate_status: Mapped[str] = mapped_column(String(32), default="none")
    critic_gate_waived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    critic_gate_waiver_actor_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    critic_gate_waiver_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    critic_gate_waiver_ticket_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="chapters")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="chapter", cascade="all, delete-orphan")


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    narration_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_package_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    continuity_tags_json: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    critic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    critic_revision_count: Mapped[int] = mapped_column(Integer, default=0)
    critic_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    critic_waived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    critic_waiver_actor_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    critic_waiver_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chapter: Mapped["Chapter"] = relationship(back_populates="scenes")
    assets: Mapped[list["Asset"]] = relationship(back_populates="scene", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    asset_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    generation_tier: Mapped[str] = mapped_column(String(32), default="preview")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    storage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeline_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scene: Mapped["Scene"] = relationship(back_populates="assets")


class CriticReport(Base):
    __tablename__ = "critic_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    score: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    dimensions_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    issues_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    recommendations_json: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    continuity_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    baseline_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    prior_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("critic_reports.id", ondelete="SET NULL"),
        nullable=True,
    )
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RevisionIssue(Base):
    __tablename__ = "revision_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    critic_report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("critic_reports.id", ondelete="CASCADE"),
        index=True,
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    code: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    refs_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    waiver_actor_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    waiver_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    waiver_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    service_type: Mapped[str] = mapped_column(String(64))
    units: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    external_request_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NarrationTrack(Base):
    __tablename__ = "narration_tracks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    voice_config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimelineVersion(Base):
    __tablename__ = "timeline_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_name: Mapped[str] = mapped_column(String(128))
    timeline_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    render_status: Mapped[str] = mapped_column(String(32), default="draft")
    output_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MusicBed(Base):
    __tablename__ = "music_beds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(500))
    storage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_or_source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    mix_config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    payload_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GenerationArtifact(Base):
    __tablename__ = "generation_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    storage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_status: Mapped[str] = mapped_column(String(32), default="succeeded")
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramChatStudioSession(Base):
    """Chat Studio conversation state for Telegram (per workspace + Telegram chat id)."""

    __tablename__ = "telegram_chat_studio_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "telegram_chat_id", name="uq_telegram_chat_studio_tenant_chat"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(32), index=True)
    messages_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    brief_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    route: Mapped[str] = mapped_column(String(256))
    key: Mapped[str] = mapped_column(String(256))
    body_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    agent_type: Mapped[str] = mapped_column(String(64))
    schema_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
