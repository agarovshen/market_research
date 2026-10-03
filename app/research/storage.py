"""SQLAlchemy persistence for reproducible experiment results."""

from datetime import datetime

from sqlalchemy import DateTime, JSON, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base
from app.research.advanced import AdvancedResearchResult
from app.research.models import ResearchResult
from app.research.serialization import (
    decode_advanced_result, decode_result, encode_advanced_result, encode_result,
)


class ResearchExperimentRecord(Base):
    __tablename__ = "research_experiments"

    experiment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class ResearchResultRepository:
    """Store tagged JSON results in the project's existing SQLAlchemy database."""

    def __init__(self, session: Session):
        self.session = session

    def save(self, result: ResearchResult) -> None:
        self.save_many((result,))

    def save_many(self, results: tuple[ResearchResult, ...]) -> None:
        """Stage one batch with one query and one flush; caller owns commit/rollback."""
        pending = {}
        for result in results:
            experiment_id = result.definition.experiment_id
            payload = encode_result(result)
            prior = pending.get(experiment_id)
            if prior is not None and prior[1] != payload:
                raise ValueError(f"Experiment identity collision with differing result: {experiment_id}")
            pending[experiment_id] = (result, payload)
        if not pending:
            return

        existing = self.session.scalars(
            select(ResearchExperimentRecord).where(
                ResearchExperimentRecord.experiment_id.in_(tuple(pending))
            )
        ).all()
        existing_by_id = {record.experiment_id: record for record in existing}
        for experiment_id, (result, payload) in pending.items():
            record = existing_by_id.get(experiment_id)
            if record is not None:
                if record.payload != payload:
                    raise ValueError(f"Experiment identity collision with differing result: {experiment_id}")
                continue
            self.session.add(ResearchExperimentRecord(
                experiment_id=experiment_id,
                strategy_id=result.definition.strategy_id,
                symbol=result.definition.symbol,
                timeframe=result.definition.timeframe,
                status=result.status.value,
                payload=payload,
            ))
        self.session.flush()

    def get(self, experiment_id: str) -> ResearchResult | None:
        record = self.session.get(ResearchExperimentRecord, experiment_id)
        return None if record is None else decode_result(record.payload)

    def list_for_strategy(self, strategy_id: str) -> tuple[ResearchResult, ...]:
        records = self.session.scalars(
            select(ResearchExperimentRecord)
            .where(ResearchExperimentRecord.strategy_id == strategy_id)
            .order_by(ResearchExperimentRecord.experiment_id)
        ).all()
        return tuple(decode_result(record.payload) for record in records)


class ResearchAnalysisRecord(Base):
    __tablename__ = "research_analyses"

    analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class ResearchAnalysisRepository:
    """Persist deterministic advanced outputs with their source/config metadata."""

    def __init__(self, session: Session):
        self.session = session

    def save(self, result: AdvancedResearchResult) -> None:
        payload = encode_advanced_result(result)
        record = self.session.get(ResearchAnalysisRecord, result.analysis_id)
        if record is not None:
            if record.payload != payload:
                raise ValueError(f"Advanced analysis identity collision: {result.analysis_id}")
            return
        self.session.add(ResearchAnalysisRecord(
            analysis_id=result.analysis_id, method=result.method, payload=payload))
        self.session.flush()

    def get(self, analysis_id: str) -> AdvancedResearchResult | None:
        record = self.session.get(ResearchAnalysisRecord, analysis_id)
        return None if record is None else decode_advanced_result(record.payload)

    def list_for_method(self, method: str) -> tuple[AdvancedResearchResult, ...]:
        records = self.session.scalars(
            select(ResearchAnalysisRecord).where(ResearchAnalysisRecord.method == method)
            .order_by(ResearchAnalysisRecord.analysis_id)
        ).all()
        return tuple(decode_advanced_result(record.payload) for record in records)
