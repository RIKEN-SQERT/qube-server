from __future__ import annotations

from pydantic import BaseModel


class PossibleLinks(BaseModel):
    boxes: list[BoxLink]


class BoxLink(BaseModel):
    name: str
    boxtype: str
    ipaddr_wss: str


class Skews(BaseModel):
    boxes: list[BoxSkew]


class BoxSkew(BaseModel):
    box_name: str
    offset: int
