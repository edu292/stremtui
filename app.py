from asyncio import as_completed
from io import BytesIO

from httpx import AsyncClient
from pydantic import AnyHttpUrl
from textual import work
from textual.app import App, ComposeResult
from textual.containers import (
    Center,
    Horizontal,
    HorizontalScroll,
    Vertical,
    VerticalScroll,
)
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, ContentSwitcher, Input, Label, Select
from textual_image.renderable import Image as AutoRenderable
from textual_image.widget import Image

from models import Entry, Episode, Metadata, Seasons, Stream
from streaming import (
    close_session,
    get_available_streams,
    get_metadata,
    get_session_handle,
    search_catalog,
    start_download,
)


async def fetch_url(client, url):
    try:
        response = await client.get(url, timeout=120)
    except TimeoutError:
        return

    if not response.is_success:
        return
    return BytesIO(response.content)


class UrlImage(Image, Renderable=AutoRenderable):
    def __init__(self, url, **kwargs):
        super().__init__(image=None, **kwargs)
        self.url = url

    async def on_mount(self):
        self.fetch_image()

    @work(exclusive=True)
    async def fetch_image(self):
        response = await fetch_url(self.app.http_client, self.url)
        if not response:
            return

        self.image = response


class Poster(Vertical):
    BINDINGS = [('enter', 'select')]
    can_focus = True

    class Selected(Message):
        def __init__(self, entry_data) -> None:
            super().__init__()
            self.entry_data = entry_data

    def __init__(self, entry, **kwargs):
        super().__init__(**kwargs)
        self.entry = entry

    async def on_mount(self) -> None:
        self.fetch_image()

    @work(exclusive=True)
    async def fetch_image(self):
        response = await fetch_url(self.app.http_client, self.data['poster'])
        if not response:
            return
        await self.remove_children()
        await self.mount(Image(response))

    def on_click(self):
        self.post_message(self.Selected(self.data))

    def action_select(self):
        self.post_message(self.Selected(self.data))

    def compose(self) -> ComposeResult:
        yield Label(self.data['name'])


class PosterList(HorizontalScroll):
    posters_data = reactive(list[Entry])

    def __init__(
        self,
        type: Entry.Types,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        can_focus: bool | None = None,
        can_focus_children: bool | None = None,
        can_maximize: bool | None = None,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            can_focus=can_focus,
            can_focus_children=can_focus_children,
            can_maximize=can_maximize,
        )
        self.type = type

    def watch_posters_data(self, posters_data: list[Entry]):
        self.remove_children()
        self.scroll_home()
        new_posters = [Poster(entry) for entry in posters_data]
        self.mount_all(new_posters)


class EpisodeCard(Horizontal):
    can_focus = True
    BINDINGS = [('enter', 'select')]

    class Selected(Message):
        def __init__(self, coordinate) -> None:
            super().__init__()
            self.episode_coordinate = coordinate

    class Focused(Message):
        def __init__(self, overview):
            super().__init__()
            self.overview = overview

    def __init__(self, coordinate, name, thumbnail, released, overview, **kwargs):
        super().__init__(**kwargs)
        self.coordinate = coordinate
        self.episode_name = name
        self.thumbnail = thumbnail
        self.released = released
        self.overview = overview

    def on_click(self):
        self.post_message(self.Selected(self.coordinate))

    def action_select(self):
        self.post_message(self.Selected(self.coordinate))

    def on_focus(self):
        if self.overview:
            self.post_message(self.Focused(self.overview))

    def compose(self) -> ComposeResult:
        yield UrlImage(self.thumbnail)
        with Vertical(classes='episode-details'):
            yield Label(self.episode_name, classes='episode-name')
            yield Label(self.released, classes='episode-release')


class EpisodeSelector(Vertical):
    BINDINGS = [('l', 'change_season("next")'), ('h', 'change_season("previous")')]
    seasons = reactive(Seasons)

    async def on_select_changed(self, event: Select.Changed):
        episodes_scroll = self.query_one('#episodes-scroll')
        await episodes_scroll.remove_children()
        season_id = event.value
        season = self.seasons.specials if season_id == 'specials' else self.seasons[season_id]
        episode_cards = [EpisodeCard(episode) for episode in season]
        await episodes_scroll.mount_all(episode_cards)
        episodes_scroll.scroll_home()
        episode_cards[0].focus()

    def watch_seasons(self, seasons):
        options = [(f'Season {number}', number) for number in range(len(seasons))]
        if seasons.has_specials:
            options.append(('Specials', 'specials'))

        self.seasons_options = options
        seasons_select = self.query_one('#seasons-select')
        seasons_select.set_options(options)
        seasons_select.allow_black = False
        seasons_select.value = 1

    def action_change_season(self, direction):
        seasons_select = self.query_one('#seasons-select')
        selected_value = seasons_select.value
        match direction:
            case 'next':
                seasons_select.value = seasons_select.options[(selected_value + 1) % len(seasons_select.options)]
            case 'previous':
                seasons_select.value = seasons_select.options[(selected_value - 1) % len(seasons_select.options)]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == 'next-button':
            self.action_change_season('next')
        elif event.button.id == 'previous-button':
            self.action_change_season('previous')

    def compose(self) -> ComposeResult:
        controls_container = Horizontal(id='controls')
        controls_container.can_focus_children = False
        with controls_container:
            yield Button('Previous', id='previous-button')
            yield Select([], id='seasons-select')
            yield Button('Next', id='next-button')
        episodes_scroll = VerticalScroll(id='episodes-scroll')
        episodes_scroll.can_focus = False
        yield episodes_scroll


class StreamSelector(VerticalScroll):
    can_focus = False
    streams = reactive(list[str])

    class Selected(Message):
        def __init__(self, stream_id) -> None:
            super().__init__()
            self.stream_id = stream_id

    async def watch_streams(self, streams_old, streams_new):
        if streams_old[0] != streams_new[0]:
            await self.remove_children()
            start_index = 0
        else:
            start_index = len(streams_old) - 1

        stream_buttons = [
            Button(stream.title, id=f'stream-{index}')
            for index, stream in enumerate(streams_new[start_index:], start_index)
        ]
        await self.mount_all(stream_buttons)

    def on_button_pressed(self, event: Button.Pressed):
        self.post_message(self.Selected(int(event.button.id.lstrip('stream-'))))


class SelectionManager(ContentSwitcher):
    BINDINGS = [('b', 'back'), ('j', 'app.focus_next'), ('k', 'app.focus_previous')]

    def __init__(self, metadata: Metadata, **kwargs):
        initial_tab = 'stream-selector' if metadata.type != Entry.Types.SERIES else 'episode-selector'
        super().__init__(initial=initial_tab, **kwargs)
        self.metadata = metadata
        self.streams = []

    def on_mount(self):
        if self.metadata.type != Entry.Types.SERIES:
            self.fetch_streams()

    def on_episode_card_selected(self, event: EpisodeCard.Selected):
        self.fetch_streams(coordinates)
        self.current = 'stream-selector'

    def action_back(self):
        if self.metadata.type == Entry.Types.SERIES and self.current == 'stream-selector':
            self.current = 'episode-selector'
        else:
            self.screen.action_back()

    @work(exclusive=True)
    async def fetch_streams(self, coordinates=None):
        self.streams.clear()
        requests = get_available_streams(self.metadata, coordinates)
        async for request in as_completed(requests):
            streams = await request
            self.streams.extend(streams)
            self.query_one('#stream-selector').streams = self.streams

    def compose(self) -> ComposeResult:
        yield EpisodeSelector(id='episode-selector')
        yield StreamSelector(id='stream-selector')


class EntryDetails(VerticalScroll):
    can_focus = True

    def __init__(
        self, logo: AnyHttpUrl, runtime: str, year: str, imdb_rating: str, cast: list[str], summary: str, **kwargs
    ):
        super().__init__(**kwargs)
        self.logo = logo
        self.year = year
        self.imdb_rating = imdb_rating
        self.cast = cast
        self.summary = summary
        self.runtime = runtime

    def compose(self) -> ComposeResult:
        yield UrlImage(self.logo, id='logo')
        with Horizontal(classes='stats'):
            yield Label(self.runtime)
            yield Label(self.year)
            yield Label(self.imdb_rating)
        with Horizontal(classes='stats'):
            for member in self.cast:
                yield Label(member)
        yield Label(self.summary, id='summary')


class DetailsScreen(Screen):
    BINDINGS = [('b', 'back')]

    def __init__(self, entry, **kwargs) -> None:
        super().__init__(**kwargs)
        self.entry = entry

    async def on_mount(self):
        self.fetch_metadata()

    @work(exclusive=True)
    async def fetch_metadata(self):
        metadata = await get_metadata(self.app.http_client, self.entry)
        content = Horizontal(
            EntryDetails(
                metadata.logo, metadata.runtime, metadata.year, metadata.imdb_rating, metadata.cast, metadata.summary
            ),
            SelectionManager(metadata),
            id='content',
        )
        await self.mount(content)

    def on_stream_selector_submitted(self, event: StreamSelector.Submitted):
        with self.app.suspend():
            start_download(self.app.torrent_session_handle, event.stream_id)

    def action_back(self):
        self.app.pop_screen()

    def on_episode_card_focused(self, event: EpisodeCard.Focused):
        self.query_one('#summary').content = event.overview


class MainScreen(Screen):
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.blur()
        async for response in as_completed(search_catalog(self.app.http_client, event.value)):
            entry_type, entry = await response
            self.query_one(f'#{entry_type}-posters').posters_data = entry

    def on_poster_selected(self, event: Poster.Selected):
        self.app.push_screen(DetailsScreen(event.entry_data))

    def compose(self) -> ComposeResult:
        with Center():
            yield Input(id='Search')
        for entry_type in Entry.Types:
            yield PosterList(id=f'{entry_type}-posters')


class StremtuiApp(App):
    CSS_PATH = 'style.css'

    async def on_mount(self):
        self.http_client = AsyncClient(follow_redirects=True)
        self.torrent_session_handle = await get_session_handle(self.http_client)
        self.push_screen(MainScreen())

    async def on_unmount(self):
        await self.http_client.aclose()
        await close_session(self.torrent_session_handle)


if __name__ == '__main__':
    app = StremtuiApp()
    app.run()
