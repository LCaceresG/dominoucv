"""Modelos de base de datos.

Idea clave para el largo plazo: las PARTIDAS son la fuente de verdad.
Los ratings y estadísticas guardados en `players` se pueden recalcular en cualquier
momento repitiendo todas las partidas en orden (ver services.recalculate_all).
"""
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .elo import INITIAL_RATING


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ID público y estable del jugador, p. ej. DOM-000123 (para carnet, QR, búsquedas)
    public_id: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    search_name: Mapped[str] = mapped_column(String(100), default="", index=True)  # sin tildes, minúsculas
    # estudiante | egresado | profesor | externo (no pertenece a la universidad) | sin_definir (falta el dato)
    category: Mapped[str] = mapped_column(String(12), default="externo", index=True)
    university: Mapped[str | None] = mapped_column(String(120), index=True)
    career: Mapped[str | None] = mapped_column(String(120))  # carrera, escuela o club (opcional)
    city: Mapped[str | None] = mapped_column(String(80), index=True)
    country: Mapped[str | None] = mapped_column(String(2))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    rating: Mapped[float] = mapped_column(Float, default=INITIAL_RATING)
    peak_rating: Mapped[float] = mapped_column(Float, default=INITIAL_RATING)
    # Cambio de ELO ganado/perdido en torneos que aún no terminan; se suma a `rating` al cerrarse el torneo
    rating_pending: Mapped[float] = mapped_column(Float, default=0.0)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    points_for: Mapped[int] = mapped_column(Integer, default=0)
    points_against: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Competition(Base):
    """Una liga o un torneo. `kind` solo es una etiqueta; `format` define cómo se juega."""

    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="liga")  # liga | torneo
    # libre | suizo | eliminacion | grupos_eliminacion | doble_eliminacion | suizo_final | suizo_gran_final
    format: Mapped[str] = mapped_column(String(24), default="libre")
    # Solo para suizo_gran_final: la final 1.º vs 2.º se juega únicamente si hay empate por el primer lugar
    final_only_on_tie: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[str | None] = mapped_column(String(120))
    season: Mapped[str | None] = mapped_column(String(40))
    start_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | active | finished
    team_size: Mapped[int] = mapped_column(Integer, default=2)
    points_per_win: Mapped[int] = mapped_column(Integer, default=3)
    points_per_draw: Mapped[int] = mapped_column(Integer, default=1)
    target_points: Mapped[int] = mapped_column(Integer, default=100)
    swiss_rounds: Mapped[int | None] = mapped_column(Integer)
    groups_count: Mapped[int | None] = mapped_column(Integer)
    qualifiers: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompetitionMember(Base):
    """Jugadores inscritos en una competencia de formato libre."""

    __tablename__ = "competition_members"
    __table_args__ = (UniqueConstraint("competition_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)

    player: Mapped[Player] = relationship()


class Entry(Base):
    """Participante de un torneo: una pareja (o un jugador si team_size = 1)."""

    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    player1_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    player2_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), index=True)
    seed: Mapped[int | None] = mapped_column(Integer)
    group_label: Mapped[str | None] = mapped_column(String(4))
    # Pareja que dejó el torneo: no sale en la tabla, pero sus partidas ya jugadas cuentan para sus rivales
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)

    player1: Mapped[Player] = relationship(foreign_keys=[player1_id])
    player2: Mapped[Player | None] = relationship(foreign_keys=[player2_id])

    @property
    def players(self) -> list[Player]:
        return [p for p in (self.player1, self.player2) if p is not None]


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int | None] = mapped_column(ForeignKey("competitions.id"), index=True)
    stage: Mapped[str] = mapped_column(String(8), default="free")  # free | swiss | group | ko
    bracket: Mapped[str] = mapped_column(String(2), default="")  # K | W | L | F | T
    round: Mapped[int | None] = mapped_column("round_no", Integer)
    slot: Mapped[int | None] = mapped_column("slot_no", Integer)
    group: Mapped[str | None] = mapped_column("group_label", String(4))
    src_a: Mapped[str | None] = mapped_column(String(24))
    src_b: Mapped[str | None] = mapped_column(String(24))
    entry_a_id: Mapped[int | None] = mapped_column(ForeignKey("entries.id"), index=True)
    entry_b_id: Mapped[int | None] = mapped_column(ForeignKey("entries.id"), index=True)
    played_at: Mapped[date | None] = mapped_column(Date, index=True)
    target_points: Mapped[int] = mapped_column(Integer, default=100)
    points_a: Mapped[int | None] = mapped_column(Integer)
    points_b: Mapped[int | None] = mapped_column(Integer)
    # pending (sin resultado) | valid | void (anulada) | bye (pase libre)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    competition: Mapped[Competition | None] = relationship()
    entry_a: Mapped[Entry | None] = relationship(foreign_keys=[entry_a_id])
    entry_b: Mapped[Entry | None] = relationship(foreign_keys=[entry_b_id])
    players: Mapped[list["MatchPlayer"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class MatchPlayer(Base):
    __tablename__ = "match_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team: Mapped[str] = mapped_column(String(1))  # "A" o "B"
    rating_before: Mapped[float] = mapped_column(Float, default=0.0)
    rating_after: Mapped[float] = mapped_column(Float, default=0.0)
    delta: Mapped[float] = mapped_column(Float, default=0.0)

    match: Mapped[Match] = relationship(back_populates="players")
    player: Mapped[Player] = relationship()


class SocialPost(Base):
    """Publicación de las redes (Instagram @dominoucv) que se muestra en la pestaña Redes y en Novedades."""

    __tablename__ = "social_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    posted_on: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300), nullable=True)  # enlace a la publicación original
    image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    image_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    image_rev: Mapped[int] = mapped_column(Integer, default=0)  # sube al cambiar la imagen (evita caché vieja)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
