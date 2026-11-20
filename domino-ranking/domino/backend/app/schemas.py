"""Esquemas de entrada/salida de la API."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CATEGORIES = ("estudiante", "egresado", "profesor", "externo", "sin_definir")
FORMATS = (
    "libre",
    "suizo",
    "eliminacion",
    "grupos_eliminacion",
    "doble_eliminacion",
    "suizo_final",
    "suizo_gran_final",
)
Category = Literal["estudiante", "egresado", "profesor", "externo", "sin_definir"]


def _clean(v: str | None) -> str | None:
    v = v.strip() if v else None
    return v or None


class PlayerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    category: Category = "externo"
    university: str | None = Field(default=None, max_length=120)
    career: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, min_length=2, max_length=2)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return " ".join(v.split())


class PlayerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    category: Category | None = None
    university: str | None = Field(default=None, max_length=120)
    career: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=2)
    active: bool | None = None


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    name: str
    category: str
    university: str | None
    career: str | None
    city: str | None
    country: str | None
    active: bool
    rating: float
    peak_rating: float
    rating_pending: float = 0.0
    games_played: int
    wins: int
    draws: int
    losses: int
    points_for: int
    points_against: int


class CompetitionCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    kind: Literal["liga", "torneo"] = "liga"
    format: Literal[
        "libre", "suizo", "eliminacion", "grupos_eliminacion", "doble_eliminacion", "suizo_final", "suizo_gran_final"
    ] = "libre"
    final_only_on_tie: bool = False
    location: str | None = Field(default=None, max_length=120)
    season: str | None = Field(default=None, max_length=40)
    start_date: date | None = None
    team_size: int = Field(default=2, ge=1, le=2)
    points_per_win: int = Field(default=3, ge=1, le=10)
    points_per_draw: int = Field(default=1, ge=0, le=5)
    target_points: int = Field(default=100, ge=10, le=500)
    swiss_rounds: int | None = Field(default=None, ge=1, le=15)
    groups_count: int | None = Field(default=None, ge=2, le=8)
    qualifiers: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def _check(self):
        self.name = " ".join(self.name.split())
        self.location = _clean(self.location)
        self.season = _clean(self.season)
        if self.format == "suizo_final" and self.qualifiers not in (None, 4, 8):
            raise ValueError("Los clasificados a la fase final deben ser 4 o 8")
        if self.format == "grupos_eliminacion" and self.qualifiers is not None and self.qualifiers > 4:
            raise ValueError("Pueden clasificar de 1 a 4 por grupo")
        return self


class CompetitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    location: str | None = Field(default=None, max_length=120)
    season: str | None = Field(default=None, max_length=40)
    start_date: date | None = None


class MemberAdd(BaseModel):
    public_id: str


class EntryCreate(BaseModel):
    players: list[str] = Field(min_length=1, max_length=2, description="IDs públicos de los jugadores")
    name: str | None = Field(default=None, max_length=160)


class MatchCreate(BaseModel):
    team_a: list[str] = Field(min_length=1, max_length=2, description="IDs públicos, p. ej. DOM-000001")
    team_b: list[str] = Field(min_length=1, max_length=2)
    points_a: int = Field(ge=0, le=1000)
    points_b: int = Field(ge=0, le=1000)
    competition_id: int | None = None
    target_points: int | None = Field(default=None, ge=10, le=500)
    played_at: date | None = None

    @model_validator(mode="after")
    def check(self):
        self.team_a = [i.strip().upper() for i in self.team_a]
        self.team_b = [i.strip().upper() for i in self.team_b]
        if len(self.team_a) != len(self.team_b):
            raise ValueError("Ambas parejas deben tener el mismo número de jugadores")
        everyone = self.team_a + self.team_b
        if len(set(everyone)) != len(everyone):
            raise ValueError("Un jugador no puede aparecer dos veces en la partida")
        return self


def _clean_link(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if not v.lower().startswith(("https://", "http://")):
        raise ValueError("El enlace debe empezar por https://")
    return v


class SocialPostCreate(BaseModel):
    posted_on: date | None = None
    title: str | None = Field(default=None, max_length=120)
    summary: str = Field(min_length=1, max_length=2000)
    link: str | None = Field(default=None, max_length=300)
    image_data: str | None = Field(default=None, description="Imagen como data URL (jpeg, png o webp)")

    @field_validator("link")
    @classmethod
    def _link(cls, v):
        return _clean_link(v)

    @field_validator("title")
    @classmethod
    def _title(cls, v):
        return _clean(v)

    @field_validator("summary")
    @classmethod
    def _summary(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Escribe el resumen de la publicación")
        return v


class SocialPostUpdate(BaseModel):
    posted_on: date | None = None
    title: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, min_length=1, max_length=2000)
    link: str | None = Field(default=None, max_length=300)
    image_data: str | None = None
    remove_image: bool = False

    @field_validator("link")
    @classmethod
    def _link(cls, v):
        return _clean_link(v)


class ResultUpdate(BaseModel):
    points_a: int = Field(ge=0, le=1000)
    points_b: int = Field(ge=0, le=1000)
    played_at: date | None = None
