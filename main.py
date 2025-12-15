from time import sleep
import subprocess
from pathlib import Path
from collections import defaultdict
from curl_cffi import requests
from libtorrent import session, parse_magnet_uri, torrent_flags

CONTENT_TYPES = ["series", "movie"]
CINEMATA_URL = "https://v3-cinemeta.strem.io"
search = input()

catalog = {}
for content_type in CONTENT_TYPES:
    entries_in_type = requests.get(
        f"{CINEMATA_URL}/catalog/{content_type}/top/search={search}.json"
    ).json()["metas"]
    catalog[content_type] = entries_in_type
    print("=" * 5, content_type.upper(), "=" * 5)
    for index, entry in enumerate(entries_in_type):
        print(f"{index} - {entry['name']}")
    print("-" * 20)
    print()

catalog_selected_type = catalog[CONTENT_TYPES[int(input())]]
selected_entry = catalog_selected_type[int(input())]
item_id = selected_entry["imdb_id"]

metadata = requests.get(
    f"{CINEMATA_URL}/meta/{selected_entry['type']}/{item_id}.json"
).json()["meta"]

print(metadata["description"])

if selected_entry["type"] == "series":
    seasons = defaultdict(list)
    for video in metadata["videos"]:
        seasons[video["season"]].append(video)
    selected_season = int(input("select a season number:"))
    episodes = seasons[selected_season]
    for episode in episodes:
        print(episode["name"])
        print(episode["overview"])
    selected_episode = int(input("select an episode: "))
    item_id += f":{selected_season}:{selected_episode}"

available_streams = requests.get(
    f"https://torrentio.strem.fun/stream/{selected_entry['type']}/{item_id}.json"
).json()["streams"]
for index, entry in enumerate(available_streams):
    print(f"{index} - {entry['title']}")
selected_stream = available_streams[int(input())]
magnetic_link = f"magnet:?xt=urn:btih:{selected_stream['infoHash']}"

session_handle = session()

params = parse_magnet_uri(magnetic_link)
params.save_path = "."
params.flags |= torrent_flags.paused
params.flags |= torrent_flags.sequential_download

torrent_handle = session_handle.add_torrent(params)

print("Downloading Metadata...")
while not torrent_handle.status().has_metadata:
    sleep(1)
    print(f"Peers: {torrent_handle.status().num_peers}")

torrent_info = torrent_handle.torrent_file()
num_files = torrent_info.num_files()
files = torrent_info.files()
priorities = [0] * num_files
for i in range(num_files):
    if files.file_name(i) == selected_stream["behaviorHints"]["filename"]:
        wanted_file_index = i
        wanted_file_extension = Path(files.file_path(i)).suffix
        priorities[i] = 1
        break

base_folder = Path(__file__).parent.resolve()
stream_buffer_path = base_folder / f"stream_buffer{wanted_file_extension}"
torrent_handle.prioritize_files(priorities)
torrent_handle.rename_file(wanted_file_index, str(stream_buffer_path))
torrent_handle.resume()

while torrent_handle.status().total_download < 1024 * 80:
    sleep(1)
subprocess.Popen(["mpv", stream_buffer_path, "--keep-open"])
while not torrent_handle.status().is_seeding:
    sleep(1)
