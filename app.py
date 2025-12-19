from io import BytesIO

from httpx import AsyncClient
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
from textual.widgets import Button, ContentSwitcher, Footer, Input, Label, Select
from textual_image.widget import Image

from streaming import (
    close_session,
    get_available_streams,
    get_metadata,
    get_session_handle,
    search_catalog,
    start_download,
)


async def fetch_url(url):
    async with AsyncClient() as client:
        response = await client.get(url)
        if not response.is_success:
            return
        return BytesIO(response.content)


class UrlImage(Image):
    def __init__(self, url, **kwargs):
        super().__init__(image=None, **kwargs)
        self.url = url

    async def on_mount(self):
        response = await fetch_url(self.url)
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

    def __init__(self, data, **kwargs):
        self.data = data
        super().__init__(**kwargs)

    async def on_mount(self) -> None:
        response = await fetch_url(self.data['poster'])
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
    posters_data = reactive(list)

    def __init__(self, posters_data=None, **kwargs) -> None:
        super().__init__(**kwargs)
        if posters_data:
            self.posters_data = posters_data

    def watch_posters_data(self, posters_data):
        self.remove_children()
        self.scroll_home()
        new_posters = [Poster(data) for data in posters_data]
        self.mount_all(new_posters)


class EpisodeCard(Horizontal):
    can_focus = True

    class Submitted(Message):
        def __init__(self, season_id, episode_id) -> None:
            super().__init__()

    def __init__(self, season_id, episode_data, **kwargs):
        super().__init__(**kwargs)
        self.season_id = season_id
        self.episode_data = episode_data

    def on_click(self):
        self.post_message(self.Submitted(self.season_id, self.episode_id))

    def compose(self) -> ComposeResult:
        yield UrlImage(self.episode_data['thumbnail'])
        with Horizontal(classes='episode-details'):
            yield Label(self.episode_data['name'], classes='episode-name')
            yield Label(self.episode_data['released'], classes='episode-release')


class EpisodeSelector(Vertical):
    def __init__(self, seasons_data, **kwargs) -> None:
        super().__init__(**kwargs)
        self.seasons_data = seasons_data

    def on_select_changed(self, event: Select.Changed):
        episodes_scroll = self.query_one('#episodes-scroll')
        episodes_scroll.remove_children()
        episode_cards = []
        season_id = event.value
        for episode_data in self.seasons_data[season_id]:
            episode_cards.append(EpisodeCard(season_id, episode_data))
        episodes_scroll.mount_compose(episode_cards)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Button('Previous', disabled=True)
            options = [(f'Season {number}', number) for number in range(1, len(self.seasons_data))]
            options[-1] = ('Special', 0)
            season_selector = Select(options)
            yield season_selector
            season_selector.value = options[0][1]
            yield Button('Next')
        yield VerticalScroll(id='episodes-scroll')


class StreamSelector(VerticalScroll):
    item_id = reactive(str)

    class Submitted(Message):
        def __init__(self, stream_data) -> None:
            super().__init__()
            self.stream_data = stream_data

    def __init__(self, item_type, item_id=None, **kwargs):
        super().__init__(**kwargs)
        self.item_type = item_type
        if item_id:
            self.item_id = item_id

    def watch_item_id(self, item_id):
        self.remove_children()
        self.streams = get_available_streams(item_id, self.item_type)
        stream_buttons = [Button(stream['name'], id=str(index)) for index, stream in enumerate(self.streams)]
        self.mount_all(stream_buttons)

    def on_button_pressed(self, event: Button.Pressed):
        stream_data = self.streams[int(event.button.id)]
        self.post_message(self.Submitted(stream_data))


class DetailsScreen(Screen):
    def __init__(self, entry, **kwargs) -> None:
        super().__init__(**kwargs)
        self.metadata = get_metadata(entry)

    def on_stream_selector_submitted(self, event: StreamSelector.Submitted):
        with self.app.suspend():
            start_download(self.app.torrent_session_handle, event.stream_data)

    def compose(self) -> ComposeResult:
        with Horizontal(id='content'):
            with VerticalScroll(id='details'):
                yield UrlImage(self.metadata['logo'], id='logo')
                with Horizontal(classes='stats'):
                    yield Label(self.metadata.get('runtime', ''))
                    yield Label(self.metadata['year'])
                    yield Label(self.metadata['imdbRating'])
                with Horizontal(classes='stats'):
                    for member in self.metadata['cast']:
                        yield Label(member)
                yield Label(self.metadata['description'], id='summary')
            selectors_switcher = ContentSwitcher(id='selectors-switcher')
            with selectors_switcher:
                stream_selector = StreamSelector(self.metadata['type'])
                yield stream_selector
                if self.metadata['type'] == 'movie':
                    stream_selector.item_id = self.metadata['imdb_id']
                    selectors_switcher.current(stream_selector)
                else:
                    episode_selector = EpisodeSelector(self.metadata['seasons_data'])
                    yield episode_selector
                    selectors_switcher.current(episode_selector)


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        with Center():
            yield Input(id='Search')
        yield PosterList(id='movies')
        yield PosterList(id='series')
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.blur()
        entries = search_catalog(event.value)
        for content_type, data in entries.items():
            self.query_one(f'{content_type}').posters_data = data

    def on_poster_selected(self, event: Poster.Selected):
        self.app.push_screen(DetailsScreen(event.entry_data))


class StremtuiApp(App):
    BINDINGS = [
        ('h', 'focus_left', 'Left'),
        ('l', 'focus_right', 'Right'),
        ('k', 'focus_up', 'Up'),
        ('j', 'focus_down', 'Down'),
    ]
    CSS_PATH = 'style.css'

    def __init__(self):
        super().__init__()
        self.torrent_session_handle = get_session_handle()

    def on_mount(self):
        self.push_screen(MainScreen())

    def on_unmount(self):
        close_session(self.torrent_session_handle)


if __name__ == '__main__':
    app = StremtuiApp()
    app.run()
