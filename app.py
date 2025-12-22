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
from textual.widgets import Button, ContentSwitcher, Input, Label, Select
from textual_image.renderable import Image as AutoRenderable
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
        response = await client.get(url, follow_redirects=True)
        if not response.is_success:
            return
        return BytesIO(response.content)


class UrlImage(Image, Renderable=AutoRenderable):
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
    BINDINGS = [('enter', 'select')]

    class Selected(Message):
        def __init__(self, episode_id) -> None:
            super().__init__()
            self.episode_id = episode_id

    def __init__(self, episode_data, **kwargs):
        super().__init__(**kwargs)
        self.episode_data = episode_data

    def on_click(self):
        self.post_message(self.Selected(self.episode_data['id']))

    def action_select(self):
        self.post_message(self.Selected(self.episode_data['id']))

    def compose(self) -> ComposeResult:
        yield UrlImage(self.episode_data['thumbnail'])
        with Vertical(classes='episode-details'):
            yield Label(self.episode_data['name'], classes='episode-name')
            yield Label(self.episode_data['released'], classes='episode-release')


class EpisodeSelector(Vertical):
    BINDINGS = [('l', 'change_season("next")'), ('h', 'change_season("previous")')]
    seasons_data = reactive([])

    def on_select_changed(self, event: Select.Changed):
        episodes_scroll = self.query_one('#episodes-scroll')
        episodes_scroll.remove_children()
        season_id = event.value
        episode_cards = [EpisodeCard(episode_data) for episode_data in self.seasons_data[season_id]]
        episodes_scroll.mount_all(episode_cards)
        episodes_scroll.scroll_home()
        episode_cards[0].focus()

    def watch_seasons_data(self, seasons_data):
        if not seasons_data:
            return
        options = [(f'Season {number}', number) for number in range(1, len(seasons_data))]
        if seasons_data[0]:
            options.append(('Special', 0))
            self.has_special = True
        else:
            self.has_special = False
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
                if self.has_special and selected_value == len(self.seasons_data) - 1:
                    seasons_select.value = 0
                else:
                    seasons_select.value += 1
            case 'previous':
                if self.has_special and selected_value == 0:
                    seasons_select.value = len(self.seasons_data) - 1
                else:
                    seasons_select.value -= 1

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
    item_id = reactive('', init=False)

    class Submitted(Message):
        def __init__(self, stream_data) -> None:
            super().__init__()
            self.stream_data = stream_data

    def __init__(self, item_type, **kwargs):
        super().__init__(**kwargs)
        self.item_type = item_type

    async def watch_item_id(self, item_id):
        await self.remove_children()
        self.streams = get_available_streams(item_id, self.item_type)
        stream_buttons = [Button(stream['title'], id=f'stream-{index}') for index, stream in enumerate(self.streams)]
        await self.mount_all(stream_buttons)
        stream_buttons[0].focus()

    def on_button_pressed(self, event: Button.Pressed):
        stream_data = self.streams[int(event.button.id.lstrip('stream-'))]
        self.post_message(self.Submitted(stream_data))


class SelectionManager(ContentSwitcher):
    BINDINGS = [('b', 'back'), ('j', 'app.focus_next'), ('k', 'app.focus_previous')]

    def __init__(self, entry_type, entry_id, seasons_data=None, **kwargs):
        initial_tab = 'stream-selector' if entry_type == 'movie' else 'episode-selector'
        super().__init__(initial=initial_tab, **kwargs)
        self.entry_type = entry_type
        self.entry_id = entry_id
        self.seasons_data = seasons_data

    def on_mount(self):
        if self.entry_type == 'movie':
            self.query_one('#stream-selector').item_id = self.entry_id
            self.current = 'stream-selector'
        else:
            self.query_one('#episode-selector').seasons_data = self.seasons_data
            self.current = 'episode-selector'

    def on_episode_card_selected(self, event: EpisodeCard.Selected):
        self.query_one('#stream-selector').item_id = event.episode_id
        self.current = 'stream-selector'

    def action_back(self):
        if self.entry_type == 'series' and self.current == 'stream-selector':
            self.current = 'episode-selector'
        else:
            self.screen.action_back()

    def compose(self) -> ComposeResult:
        yield EpisodeSelector(id='episode-selector')
        yield StreamSelector(self.entry_type, id='stream-selector')


class DetailsScreen(Screen):
    BINDINGS = [('b', 'back')]

    def __init__(self, entry, **kwargs) -> None:
        super().__init__(**kwargs)
        self.metadata = get_metadata(entry)

    def on_stream_selector_submitted(self, event: StreamSelector.Submitted):
        with self.app.suspend():
            start_download(self.app.torrent_session_handle, event.stream_data)

    def action_back(self):
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        with Horizontal(id='content'):
            details = VerticalScroll(id='details')
            details.can_focus = False
            with details:
                yield UrlImage(self.metadata['logo'], id='logo')
                with Horizontal(classes='stats'):
                    yield Label(self.metadata.get('runtime', ''))
                    yield Label(self.metadata['year'])
                    yield Label(self.metadata['imdbRating'])
                with Horizontal(classes='stats'):
                    for member in self.metadata['cast']:
                        yield Label(member)
                yield Label(self.metadata['description'], id='summary')
            yield SelectionManager(self.metadata['type'], self.metadata['imdb_id'], self.metadata.get('seasons_data'))


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        with Center():
            yield Input(id='Search')
        yield PosterList(id='movie-posters')
        yield PosterList(id='series-posters')

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.blur()
        entries = search_catalog(event.value)
        for content_type, data in entries.items():
            self.query_one(f'#{content_type}-posters').posters_data = data

    def on_poster_selected(self, event: Poster.Selected):
        self.app.push_screen(DetailsScreen(event.entry_data))


class StremtuiApp(App):
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
