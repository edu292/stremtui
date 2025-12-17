from collections.abc import Container
from io import BytesIO
import asyncio
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.containers import (
    HorizontalScroll,
    Center,
    Vertical,
    Horizontal,
    VerticalScroll,
)
from textual.widgets import Button, Footer, Input, Label
from textual.message import Message
from textual_image.widget import Image
from httpx import AsyncClient

from streaming import (
    get_available_streams,
    get_session_handle,
    search_catalog,
    get_metadata,
    close_session,
    start_download,
)


async def fetch_url(url):
    async with AsyncClient() as client:
        response = await client.get(url)
        if not response.is_success:
            return
        return BytesIO(response.content)


class Poster(Vertical):
    BINDINGS = [("enter", "select")]
    can_focus = True

    class Selected(Message):
        def __init__(self, entry_data) -> None:
            super().__init__()
            self.entry_data = entry_data

    def __init__(self, data, **kwargs):
        self.data = data
        super().__init__(**kwargs)

    async def on_mount(self) -> None:
        response = await fetch_url(self.data["poster"])
        if not response:
            return
        await self.remove_children()
        await self.mount(Image(response))

    def on_click(self):
        self.post_message(self.Selected(self.data))

    def action_select(self):
        self.post_message(self.Selected(self.data))

    def compose(self) -> ComposeResult:
        yield Label(self.data["name"])


class PosterList(HorizontalScroll):
    def update_posters(self, posters_data):
        self.remove_children()
        self.scroll_home()
        new_posters = [Poster(data) for data in posters_data]
        self.mount_all(new_posters)


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        with Center():
            yield Input(id="Search")
        yield PosterList(id="movies")
        yield PosterList(id="series")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.blur()
        entries = search_catalog(event.value)
        self.query_one("#movies").update_posters(entries["movie"])
        self.query_one("#series").update_posters(entries["series"])

    def on_poster_selected(self, event: Poster.Selected):
        self.app.push_screen(EntryDetailsScreen(event.entry_data))


class EntryDetailsScreen(Screen):
    def __init__(self, entry, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data = get_metadata(entry)

    async def on_mount(self):
        self.query_one("#logo").image = await fetch_url(self.data["logo"])

    def on_button_pressed(self, event: Button.Pressed):
        with self.app.suspend():
            start_download(self.app.torrent_session_handle, event.button.data)

    def compose(self) -> ComposeResult:
        with Horizontal(id="content"):
            with VerticalScroll(id="details"):
                yield Image(id="logo")
                with Horizontal(classes="stats"):
                    yield Label(self.data.get("runtime", ''))
                    yield Label(self.data["year"])
                    yield Label(self.data["imdbRating"])
                with Horizontal(classes="stats"):
                    for member in self.data["cast"]:
                        yield Label(member)
                yield Label(self.data["description"], id="summary")
            with VerticalScroll(id="streams"):
                if self.data["type"] == "movie":
                    for stream in get_available_streams(self.data):
                        button = Button(stream["title"])
                        button.data = stream
                        yield button


class StremtuiApp(App):
    BINDINGS = [
        ("h", "focus('left')", "Left"),
        ("l", "focus('right')", "Right"),
        ("k", "focus('up')", "Up"),
        ("j", "focus('down')", "Down"),
    ]
    CSS_PATH = "style.css"

    def __init__(self):
        super().__init__()
        self.torrent_session_handle = get_session_handle()

    def on_mount(self):
        self.push_screen(MainScreen())

    def on_unmount(self):
        close_session(self.torrent_session_handle)


if __name__ == "__main__":
    app = StremtuiApp()
    app.run()
