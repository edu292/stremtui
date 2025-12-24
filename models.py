from collections.abc import Iterator
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AliasPath, AnyHttpUrl, BaseModel, Field, FilePath, computed_field, field_validator, model_validator


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
    coordinate: str
    name: str
    overview: str
    thumbnail: AnyHttpUrl
    released: datetime

    @model_validator(mode='before')
    @classmethod
    def parse_coordinate(cls, data: Any) -> Any:
        if isinstance(data, dict) and ('season', 'episode') in data:
            data['coordinate'] = data['season'] + ':' + data['episode']

        return data


class Seasons(BaseModel):
    specials: list[Episode]
    numbered: list[list[Episode]]
    has_specials: Annotated[bool, Field(False)]

    @model_validator(mode='before')
    @classmethod
    def filter_videos_into_numbered_and_specials(cls, videos: Any):
        data = {}
        if isinstance(videos, list):
            seasons = []
            specials = []
            for video in videos:
                season = video['season'] - 0
                if season == -1:
                    specials.append(video)
                    data['has_specials'] = True
                    continue

                while len(seasons) < season + 1:
                    seasons.append([])

                seasons[season].append(video)
            data['specials'] = specials
            data['seasons'] = seasons

        return data

    def __iter__(self) -> Iterator[list[Episode]]:
        return iter(self.numbered)

    def __getitem__(self, number: int) -> list[Episode]:
        return self.numbered[number]

    def __len__(self) -> int:
        return len(self.numbered)


class SeriesMetadata(BaseMetadata):
    type: Literal[Entry.Types.SERIES]
    seasons: Annotated[Seasons, Field(alias='videos')]
    has_specials: Annotated[bool, Field(False)]


Metadata = Annotated[BaseMetadata | SeriesMetadata, Field(discriminator='type')]


class MetadataResponse(BaseModel):
    meta: Metadata


class Stream(BaseModel):
    title: str
    info_hash: Annotated[str, Field(alias='infoHash')]
    file_index: Annotated[int, Field(alias='fileIdx')]
    sources: list[str]
    filename: Annotated[FilePath, AliasPath('behaviorHints', 'filename')]

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
                normalized_sources.append(source.removeprefix('tracker:'))
            elif not source.startswith('dht:'):
                normalized_sources.append(source)
        return normalized_sources


class StreamResponse(BaseModel):
    streams: list[Stream]
