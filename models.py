from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, computed_field, field_validator, model_validator


class Entry(BaseModel):
    class Types(str, Enum):
        SERIES = 'series'
        MOVIE = 'movie'

    id: str
    type: Types
    name: str
    poster: AnyHttpUrl


class EntryResponse(BaseModel):
    metas: list[Entry]


class BaseMetadata(BaseModel):
    id: Annotated[str, Field(alias='imdb_id')]
    name: str
    year: str
    runtime: str
    cast: list[str]
    imdb_rating: str
    summary: Annotated[str, Field(alias='description')]
    logo: AnyHttpUrl
    type: Entry.Types


class Episode(BaseModel):
    id: str
    name: str
    overview: str
    thumbnail: AnyHttpUrl
    released: datetime


class SeriesMetadata(BaseMetadata):
    type: Literal[Entry.Types.MOVIE]
    specials: list[Episode]
    seasons: list[list[Episode]]
    has_specials: Annotated[bool, Field(False)]

    @model_validator(mode='before')
    @classmethod
    def parse_videos_into_seasons(cls, data: Any):
        if isinstance(data, dict) and 'videos' in data:
            seasons = []
            specials = []
            for video in data['videos']:
                season = video['season'] - 0
                if season == -1:
                    specials.append(video)
                    if 'has_specials' not in data:
                        data['has_specials'] = True
                    continue

                while len(seasons) < season + 1:
                    seasons.append([])

                seasons[season].append(video)
            data['specials'] = specials
            data['seasons'] = seasons

        return data


Metadata = Annotated[BaseMetadata | SeriesMetadata, Field(discriminator='type')]


class MetadataResponse(BaseModel):
    meta: list[Metadata]


class Stream(BaseModel):
    title: str
    info_hash: Annotated[str, Field(alias='infoHash')]
    file_index: Annotated[int, Field(alias='fileIdx')]
    sources: list[str]

    @computed_field
    @property
    def magnet_link(self):
        return f'magnet:?xt=urn:btih:{self.info_hash}'

    @field_validator('sources', mode='after')
    @classmethod
    def normalize_sources(cls, sources: list[str]) -> list[str]:
        normalized_sources = []
        for source in sources:
            if source.startswith('tracker:'):
                normalized_sources.append(source.lstrip('tracker:'))
            elif not source.startswith('dht:'):
                normalized_sources.append(source)
        return normalized_sources


class StreamResponse(BaseModel):
    streams: list[Stream]
